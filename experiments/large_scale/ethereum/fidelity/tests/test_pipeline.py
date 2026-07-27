from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

FIDELITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FIDELITY_ROOT / "src"))

from fidelity.normalize import normalize_dataset, sql_string
from fidelity.modeling import fit_model
from fidelity.multiday import fit_multiday_topology, resolve_days
from fidelity.reconstruct import reconstruct_dataset
from fidelity.report import generate_report
from fidelity.storage import DatasetLayout, atomic_write_json, sha256_file
from fidelity.temporal import audit_snapshots, build_snapshots
from fidelity.topology import fit_topology
from fidelity.topology_prep import prepare_topology_day
from fidelity.validate import validate_dataset

try:
    import duckdb
except ImportError:
    duckdb = None


@unittest.skipIf(duckdb is None, "duckdb is not installed")
class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.layout = DatasetLayout(Path(self.temporary.name), "xatu-mainnet-fixture")
        self.layout.create()
        fixture_dir = FIDELITY_ROOT / "tests" / "fixtures"
        table_files = {
            "libp2p_synthetic_heartbeat": fixture_dir / "heartbeat.csv",
            "libp2p_connected": fixture_dir / "connected.csv",
            "libp2p_disconnected": fixture_dir / "disconnected.csv",
            "libp2p_identify": fixture_dir / "identify.csv",
        }
        connection = duckdb.connect()
        raw_files = {}
        try:
            for table, csv_path in table_files.items():
                output = self.layout.raw / f"{table}.parquet"
                source = f"read_csv_auto({sql_string(csv_path)}, header=true)"
                source_query = f"SELECT * FROM {source}"
                if table in {"libp2p_connected", "libp2p_disconnected"}:
                    source_query = (
                        "SELECT * REPLACE "
                        f"(CAST(epoch(opened) AS UINTEGER) AS opened) FROM {source}"
                    )
                connection.execute(
                    f"COPY ({source_query}) "
                    f"TO {sql_string(output)} (FORMAT PARQUET)"
                )
                raw_files[table] = {
                    "path": str(output.relative_to(self.layout.path)),
                    "url": f"fixture://{csv_path.name}",
                    "size_bytes": output.stat().st_size,
                    "sha256": sha256_file(output),
                    "verified_at": "2026-07-17T00:00:00+00:00",
                }
        finally:
            connection.close()
        atomic_write_json(
            self.layout.manifest_path,
            {
                "schema_version": 1,
                "dataset_id": self.layout.dataset_id,
                "source": "xatu",
                "network": "mainnet",
                "date": "2026-07-16",
                "created_at": "2026-07-17T00:00:00+00:00",
                "tool_version": "test",
                "raw_files": raw_files,
                "processing_runs": {},
            },
        )

    def test_end_to_end_semantics_and_provenance(self) -> None:
        normalized = normalize_dataset(self.layout)
        self.assertTrue(normalized.exists())
        run = reconstruct_dataset(self.layout, bucket_seconds=60, export_csv=True)
        report_path = generate_report(self.layout, run)
        validation_path = validate_dataset(self.layout, run)

        connection = duckdb.connect()
        try:
            g0 = connection.execute(
                "SELECT source, target FROM read_parquet(?) ORDER BY source, target",
                [str(run / "g0_edges.parquet")],
            ).fetchall()
            candidates = connection.execute(
                "SELECT source, target FROM read_parquet(?) ORDER BY source, target",
                [str(run / "candidate_edges.parquet")],
            ).fetchall()
            replacements = connection.execute(
                "SELECT source, target FROM read_parquet(?) ORDER BY source, target",
                [str(run / "replacement_edges.parquet")],
            ).fetchall()
            intervals = connection.execute(
                "SELECT target, left_censored, right_censored FROM read_parquet(?) ORDER BY target",
                [str(run / "connection_intervals.parquet")],
            ).fetchall()
            opened_at = connection.execute(
                """
                SELECT opened_at
                FROM read_parquet(?)
                WHERE event_kind = 'connected' AND remote_peer_id = 'peer:104'
                """,
                [str(normalized)],
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(len(g0), 3)
        self.assertTrue(set(g0) < set(candidates))
        self.assertEqual(set(replacements), set(candidates) - set(g0))
        self.assertNotIn(("observer:D", "peer:108"), candidates)
        self.assertIn(("peer:108", True, False), intervals)
        self.assertIn(("peer:107", False, True), intervals)
        self.assertEqual(opened_at.isoformat(), "2026-07-16T12:05:00")

        report = json.loads(report_path.read_text())
        validation = json.loads(validation_path.read_text())
        self.assertEqual(report["g0"]["edges"], 3)
        self.assertTrue(report["subset_check"]["valid"])
        self.assertEqual(
            report["connection_intervals"]["duration_ms_p50_complete"], 300000
        )
        self.assertEqual(
            report["connection_intervals"]["duration_ms_p95_complete"], 300000
        )
        self.assertTrue(report["plots_generated"])
        self.assertTrue(validation["valid"])

    def test_identify_table_is_optional(self) -> None:
        manifest = json.loads(self.layout.manifest_path.read_text())
        manifest["raw_files"].pop("libp2p_identify")
        manifest["unavailable_optional_tables"] = {
            "libp2p_identify": {"reason": "fixture intentionally unavailable"}
        }
        atomic_write_json(self.layout.manifest_path, manifest)

        normalized = normalize_dataset(self.layout)
        connection = duckdb.connect()
        try:
            kinds = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT event_kind FROM read_parquet(?)", [str(normalized)]
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertEqual(kinds, {"heartbeat", "connected", "disconnected"})

    def test_temporal_audit_and_model(self) -> None:
        normalize_dataset(self.layout)
        run = reconstruct_dataset(self.layout, bucket_seconds=60, export_csv=False)
        snapshots = build_snapshots(
            self.layout,
            run,
            bucket_seconds=60,
            session_gap_seconds=180,
        )
        audit_path = audit_snapshots(self.layout, run)
        model_dir = fit_model(
            self.layout,
            run,
            node_count=7,
            seed=1337,
            swaps_per_edge=1.0,
        )

        connection = duckdb.connect()
        try:
            snapshot_count = connection.execute(
                "SELECT count(*) FROM read_parquet(?)",
                [str(snapshots / "snapshot_summary.parquet")],
            ).fetchone()[0]
            profile_rows = connection.execute(
                """
                SELECT source, behavior_cohort
                FROM read_parquet(?) ORDER BY source
                """,
                [str(snapshots / "observer_profiles.parquet")],
            ).fetchall()
            graph_stats = connection.execute(
                """
                SELECT count(*), count(DISTINCT source) + count(DISTINCT target)
                FROM read_parquet(?)
                """,
                [str(model_dir / "full_size_snapshot.parquet")],
            ).fetchone()
        finally:
            connection.close()

        audit = json.loads(audit_path.read_text())
        validation = json.loads(
            (model_dir / "validation_report.json").read_text()
        )
        self.assertEqual(snapshot_count, 2)
        self.assertEqual(profile_rows, [("observer:A", "bounded"), ("observer:D", "bounded")])
        self.assertGreater(graph_stats[0], 0)
        self.assertTrue(audit["limitations"]["complete_overlay_observed"] is False)
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["graph"]["nodes"], 7)
        self.assertEqual(validation["graph"]["components"], 1)

    def test_propensity_topology_outputs_six_connected_snapshots(self) -> None:
        normalize_dataset(self.layout)
        run = reconstruct_dataset(self.layout, bucket_seconds=60, export_csv=False)
        build_snapshots(
            self.layout,
            run,
            bucket_seconds=60,
            session_gap_seconds=180,
        )
        audit_snapshots(self.layout, run)
        model_dir = fit_topology(
            self.layout,
            run,
            node_count=3,
            snapshot_count=5,
            step_minutes=5,
            mean_degree_min=1.0,
            mean_degree_max=2.1,
            peer_limit=4,
            peer_reserve=1,
            sample_limit=100,
            seed=1337,
        )

        connection = duckdb.connect()
        try:
            snapshot_rows = connection.execute(
                """
                SELECT snapshot_index, count(*)
                FROM read_parquet(?)
                GROUP BY snapshot_index ORDER BY snapshot_index
                """,
                [str(model_dir / "snapshots.parquet")],
            ).fetchall()
        finally:
            connection.close()

        validation = json.loads((model_dir / "validation_report.json").read_text())
        self.assertEqual([row[0] for row in snapshot_rows], list(range(6)))
        self.assertTrue(validation["valid"])
        self.assertTrue(
            all(snapshot["components"] == 1 for snapshot in validation["snapshots"])
        )
        self.assertTrue(validation["structural_constraints"]["no_per_node_degree_target"])

    def test_lightweight_topology_preparation_uses_raw_heartbeat(self) -> None:
        run = prepare_topology_day(
            self.layout,
            bucket_seconds=60,
            session_gap_seconds=180,
        )
        self.assertTrue((run / "candidate_nodes.parquet").exists())
        self.assertTrue((run / "snapshots" / "snapshot_edges.parquet").exists())
        self.assertTrue((run / "snapshots" / "heartbeat_sessions.parquet").exists())
        report = json.loads(
            (run / "snapshots" / "data_quality_report.json").read_text()
        )
        self.assertEqual(
            report["classification"]["recommended_fit_cohort"], "bounded"
        )
        self.assertFalse(report["limitations"]["complete_overlay_observed"])

    def test_multiday_model_keeps_a_separate_test_day(self) -> None:
        layouts = []
        base_manifest = json.loads(self.layout.manifest_path.read_text())
        for index, date in enumerate(("2026-07-14", "2026-07-15", "2026-07-16")):
            layout = DatasetLayout(
                Path(self.temporary.name), f"xatu-mainnet-{date}-split-{index}"
            )
            layout.create()
            raw_files = {}
            for table, entry in base_manifest["raw_files"].items():
                source = self.layout.path / entry["path"]
                destination = layout.raw / source.name
                shutil.copyfile(source, destination)
                raw_files[table] = {
                    **entry,
                    "path": str(destination.relative_to(layout.path)),
                }
            manifest = {
                **base_manifest,
                "dataset_id": layout.dataset_id,
                "date": date,
                "raw_files": raw_files,
            }
            atomic_write_json(layout.manifest_path, manifest)
            prepare_topology_day(layout, bucket_seconds=60, session_gap_seconds=180)
            layouts.append(layout)

        output = fit_multiday_topology(
            resolve_days(layouts[:1]),
            resolve_days(layouts[1:2]),
            resolve_days(layouts[2:]),
            output_root=Path(self.temporary.name) / "multiday-models",
            node_count=3,
            step_minutes=5,
            mean_degree_min=1.0,
            mean_degree_max=2.1,
            peer_limit=4,
            peer_reserve=1,
            samples_per_day=100,
            generation_seeds=(1337, 7331),
        )
        validation = json.loads((output / "validation_report.json").read_text())
        self.assertTrue(
            validation["validation_criteria"][
                "all_seeded_graphs_structurally_valid"
            ]
        )
        self.assertEqual(validation["temporal_split"]["test"], ["2026-07-16"])
        self.assertFalse(
            validation["temporal_split"]["test_used_for_selection_or_fitting"]
        )
        self.assertTrue((output / "seed-1337" / "snapshots.parquet").exists())


if __name__ == "__main__":
    unittest.main()
