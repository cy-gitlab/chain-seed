from __future__ import annotations

from pathlib import Path
from typing import Any

from .normalize import require_duckdb, sql_string
from .reconstruct import latest_run
from .storage import DatasetLayout, atomic_write_json, read_json, sha256_file, utc_now


def validate_dataset(
    layout: DatasetLayout,
    run_dir: Path | None = None,
    verify_raw_checksums: bool = True,
) -> Path:
    run = run_dir or latest_run(layout)
    manifest = read_json(layout.manifest_path)
    checks: dict[str, Any] = {}
    raw_checks = {}
    for table, entry in manifest["raw_files"].items():
        path = layout.path / entry["path"]
        exists = path.exists()
        checksum_matches = None
        if exists and verify_raw_checksums:
            checksum_matches = sha256_file(path) == entry["sha256"]
        raw_checks[table] = {
            "exists": exists,
            "size_matches": exists and path.stat().st_size == entry["size_bytes"],
            "checksum_matches": checksum_matches,
        }
    checks["raw_files"] = raw_checks

    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone = 'UTC'")
        g0 = run / "g0_edges.parquet"
        candidates = run / "candidate_edges.parquet"
        replacements = run / "replacement_edges.parquet"
        observations = layout.normalized / "normalized_observations.parquet"
        reconstruction = read_json(run / "reconstruction_manifest.json")
        snapshot = reconstruction["snapshot"]

        missing_g0 = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?) AS initial
            ANTI JOIN read_parquet(?) AS candidate
              ON initial.source = candidate.source AND initial.target = candidate.target
            """,
            [str(g0), str(candidates)],
        ).fetchone()[0]
        incorrect_replacements = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?) AS replacement
            JOIN read_parquet(?) AS initial
              ON replacement.source = initial.source AND replacement.target = initial.target
            """,
            [str(replacements), str(g0)],
        ).fetchone()[0]
        missing_evidence = connection.execute(
            """
            SELECT count(*)
            FROM read_parquet(?) AS initial
            WHERE NOT EXISTS (
                SELECT 1 FROM read_parquet(?) AS observation
                WHERE observation.event_kind = 'heartbeat'
                  AND observation.event_time >= CAST(? AS TIMESTAMP)
                  AND observation.event_time < CAST(? AS TIMESTAMP)
                  AND observation.observer_id = initial.source
                  AND observation.remote_peer_id = initial.target
            )
            """,
            [str(g0), str(observations), snapshot["bucket_start"], snapshot["bucket_end"]],
        ).fetchone()[0]
        duplicate_counts = {}
        for name, path in (
            ("g0", g0),
            ("c", candidates),
            ("c_new", replacements),
        ):
            duplicate_counts[name] = int(
                connection.execute(
                    """
                    SELECT count(*) FROM (
                        SELECT source, target, count(*) AS copies
                        FROM read_parquet(?)
                        GROUP BY source, target
                        HAVING copies > 1
                    )
                    """,
                    [str(path)],
                ).fetchone()[0]
            )
        provenance_missing = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?)
            WHERE source_table IS NULL OR source_file IS NULL
               OR source_row_number IS NULL OR source_record_id IS NULL
            """,
            [str(observations)],
        ).fetchone()[0]
        checks["graph_semantics"] = {
            "g0_missing_from_c": int(missing_g0),
            "c_new_overlaps_g0": int(incorrect_replacements),
            "g0_edges_without_snapshot_heartbeat": int(missing_evidence),
            "duplicate_edge_groups": duplicate_counts,
        }
        checks["provenance"] = {
            "normalized_rows_missing_source_locator": int(provenance_missing)
        }
    finally:
        connection.close()

    raw_valid = all(
        item["exists"]
        and item["size_matches"]
        and item["checksum_matches"] is not False
        for item in raw_checks.values()
    )
    graph = checks["graph_semantics"]
    valid = (
        raw_valid
        and graph["g0_missing_from_c"] == 0
        and graph["c_new_overlaps_g0"] == 0
        and graph["g0_edges_without_snapshot_heartbeat"] == 0
        and all(value == 0 for value in graph["duplicate_edge_groups"].values())
        and checks["provenance"]["normalized_rows_missing_source_locator"] == 0
    )
    result = {
        "schema_version": 1,
        "dataset_id": layout.dataset_id,
        "run_id": run.name,
        "validated_at": utc_now(),
        "verify_raw_checksums": verify_raw_checksums,
        "valid": valid,
        "checks": checks,
    }
    output = layout.reports / run.name / "validation_report.json"
    atomic_write_json(output, result)
    if not valid:
        raise RuntimeError(f"dataset validation failed; see {output}")
    return output
