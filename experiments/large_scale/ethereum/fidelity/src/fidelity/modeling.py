from __future__ import annotations

import csv
import hashlib
import heapq
import json
import math
import os
import random
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .normalize import require_duckdb, sql_string
from .storage import DatasetLayout, atomic_write_json, read_json, utc_now


MODEL_ALGORITHM = "xatu-ego-generative-v2"


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {name: None for name in ("p05", "p25", "p50", "p75", "p95")}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = (len(ordered) - 1) * fraction
        lower = math.floor(index)
        upper = math.ceil(index)
        if lower == upper:
            return float(ordered[lower])
        return float(
            ordered[lower]
            + (ordered[upper] - ordered[lower]) * (index - lower)
        )

    return {
        "p05": percentile(0.05),
        "p25": percentile(0.25),
        "p50": percentile(0.50),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
    }


def _distribution(
    connection: Any,
    query: str,
    parameters: list[Any] | None = None,
    denominator: int | None = None,
) -> list[dict[str, Any]]:
    rows = connection.execute(query, parameters or []).fetchall()
    total = denominator or sum(int(row[1]) for row in rows)
    return [
        {
            "value": str(row[0]) if row[0] is not None else "<unknown>",
            "count": int(row[1]),
            "fraction": int(row[1]) / total if total else 0.0,
        }
        for row in rows
    ]


def _sample_degree_sequence(
    samples: list[int], node_count: int, randomizer: random.Random
) -> list[int]:
    if not samples:
        raise RuntimeError("the selected observer cohort has no degree samples >= 2")
    degrees = [
        min(node_count - 1, max(2, int(randomizer.choice(samples))))
        for _ in range(node_count)
    ]
    if sum(degrees) % 2:
        index = randomizer.randrange(node_count)
        if degrees[index] < node_count - 1:
            degrees[index] += 1
        else:
            degrees[index] -= 1
    return degrees


def _havel_hakimi(
    degrees: list[int], randomizer: random.Random
) -> set[tuple[int, int]]:
    heap = [(-degree, randomizer.random(), node) for node, degree in enumerate(degrees)]
    heapq.heapify(heap)
    edges: set[tuple[int, int]] = set()
    while heap:
        negative_degree, _, node = heapq.heappop(heap)
        degree = -negative_degree
        if degree == 0:
            break
        if degree > len(heap):
            raise ValueError("sampled degree sequence is not graphical")
        neighbors = [heapq.heappop(heap) for _ in range(degree)]
        for neighbor_degree, _, neighbor in neighbors:
            if neighbor_degree == 0:
                raise ValueError("sampled degree sequence is not graphical")
            edge = (node, neighbor) if node < neighbor else (neighbor, node)
            if edge in edges:
                raise ValueError("Havel-Hakimi generated a duplicate edge")
            edges.add(edge)
        for neighbor_degree, _, neighbor in neighbors:
            remaining = -neighbor_degree - 1
            if remaining:
                heapq.heappush(
                    heap, (-remaining, randomizer.random(), neighbor)
                )
    return edges


def _randomize_edges(
    edges: set[tuple[int, int]],
    randomizer: random.Random,
    attempts: int,
) -> tuple[set[tuple[int, int]], int]:
    edge_list = list(edges)
    successful = 0
    if len(edge_list) < 2:
        return edges, successful
    for _ in range(attempts):
        left_index, right_index = randomizer.sample(range(len(edge_list)), 2)
        left = edge_list[left_index]
        right = edge_list[right_index]
        a, b = left
        c, d = right
        if len({a, b, c, d}) != 4:
            continue
        if randomizer.random() < 0.5:
            first = tuple(sorted((a, c)))
            second = tuple(sorted((b, d)))
        else:
            first = tuple(sorted((a, d)))
            second = tuple(sorted((b, c)))
        if first == second or first in edges or second in edges:
            continue
        edges.remove(left)
        edges.remove(right)
        edges.add(first)
        edges.add(second)
        edge_list[left_index] = first
        edge_list[right_index] = second
        successful += 1
    return edges, successful


def _components(
    node_count: int, edges: Iterable[tuple[int, int]]
) -> list[list[int]]:
    parent = list(range(node_count))
    size = [1] * node_count

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if size[left_root] < size[right_root]:
            left_root, right_root = right_root, left_root
        parent[right_root] = left_root
        size[left_root] += size[right_root]

    for left, right in edges:
        union(left, right)
    result: dict[int, list[int]] = {}
    for node in range(node_count):
        result.setdefault(find(node), []).append(node)
    return sorted(result.values(), key=len, reverse=True)


def _ensure_connected(
    node_count: int,
    edges: set[tuple[int, int]],
    randomizer: random.Random,
) -> tuple[set[tuple[int, int]], int]:
    components = _components(node_count, edges)
    added = 0
    if len(components) <= 1:
        return edges, added
    anchor = components[0]
    for component in components[1:]:
        left = randomizer.choice(anchor)
        right = randomizer.choice(component)
        edge = (left, right) if left < right else (right, left)
        edges.add(edge)
        anchor.extend(component)
        added += 1
    return edges, added


def _generate_graph(
    samples: list[int],
    node_count: int,
    seed: int,
    swaps_per_edge: float,
) -> tuple[list[int], set[tuple[int, int]], dict[str, int]]:
    randomizer = random.Random(seed)
    last_error: Exception | None = None
    for _ in range(20):
        degrees = _sample_degree_sequence(samples, node_count, randomizer)
        try:
            edges = _havel_hakimi(degrees, randomizer)
            break
        except ValueError as error:
            last_error = error
    else:
        raise RuntimeError(
            f"unable to sample a graphical degree sequence after 20 attempts: {last_error}"
        )

    attempts = int(len(edges) * max(0.0, swaps_per_edge))
    edges, successful_swaps = _randomize_edges(edges, randomizer, attempts)
    edges, bridge_edges = _ensure_connected(node_count, edges, randomizer)
    return degrees, edges, {
        "swap_attempts": attempts,
        "successful_swaps": successful_swaps,
        "connectivity_repair_edges": bridge_edges,
    }


def _write_edges(
    connection: Any,
    model_dir: Path,
    model_id: str,
    node_ids: list[str],
    edges: set[tuple[int, int]],
) -> Path:
    output = model_dir / "full_size_snapshot.parquet"
    if output.exists():
        return output
    temporary_csv = model_dir / f".full-size-edges-{os.getpid()}.csv"
    temporary_parquet = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        with temporary_csv.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("source", "target"))
            for left, right in sorted(edges):
                writer.writerow((node_ids[left], node_ids[right]))
        connection.execute(
            f"""
            COPY (
                SELECT
                    source::VARCHAR AS source,
                    target::VARCHAR AS target,
                    true AS synthetic,
                    {sql_string(model_id)}::VARCHAR AS model_id
                FROM read_csv(
                    {sql_string(temporary_csv)},
                    header=true,
                    columns={{'source': 'VARCHAR', 'target': 'VARCHAR'}}
                )
            ) TO {sql_string(temporary_parquet)}
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        temporary_parquet.replace(output)
    finally:
        temporary_csv.unlink(missing_ok=True)
        temporary_parquet.unlink(missing_ok=True)
    return output


def _model_id(
    layout: DatasetLayout,
    run_dir: Path,
    node_count: int,
    seed: int,
    swaps_per_edge: float,
) -> str:
    audit = read_json(run_dir / "snapshots" / "data_quality_report.json")
    snapshots = read_json(run_dir / "snapshots" / "snapshot_manifest.json")
    payload = {
        "dataset_id": layout.dataset_id,
        "run_id": run_dir.name,
        "classification": audit["classification"],
        "snapshot_parameters": snapshots["parameters"],
        "algorithm": MODEL_ALGORITHM,
        "node_count": node_count,
        "seed": seed,
        "swaps_per_edge": swaps_per_edge,
        "fit_cohort": "bounded",
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()
    ).hexdigest()[:12]
    return f"{MODEL_ALGORITHM}-{digest}"


def fit_model(
    layout: DatasetLayout,
    run_dir: Path,
    node_count: int | None = None,
    seed: int = 1337,
    swaps_per_edge: float = 2.0,
) -> Path:
    snapshot_dir = run_dir / "snapshots"
    audit_path = snapshot_dir / "data_quality_report.json"
    if not audit_path.exists():
        raise RuntimeError("run snapshots and audit before fitting the model")
    if swaps_per_edge < 0:
        raise ValueError("swaps_per_edge must not be negative")

    candidate_nodes = run_dir / "candidate_nodes.parquet"
    profiles = snapshot_dir / "observer_profiles.parquet"
    timeseries = snapshot_dir / "observer_timeseries.parquet"
    snapshot_edges = snapshot_dir / "snapshot_edges.parquet"
    sessions = snapshot_dir / "heartbeat_sessions.parquet"
    if not candidate_nodes.exists():
        raise RuntimeError("the reconstruction run has no candidate node table")

    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone = 'UTC'")
        all_node_rows = connection.execute(
            """
            SELECT node_id, client, client_version, asn, as_org, country_code, city, ip
            FROM read_parquet(?)
            WHERE node_role = 'remote'
            ORDER BY node_id
            """,
            [str(candidate_nodes)],
        ).fetchall()
        available_nodes = len(all_node_rows)
        requested_nodes = available_nodes if node_count is None else node_count
        if requested_nodes < 3:
            raise ValueError("node_count must be at least 3")
        if requested_nodes > available_nodes:
            raise ValueError(
                f"node_count {requested_nodes} exceeds {available_nodes} observed remote peers"
            )
        selector = random.Random(seed)
        if requested_nodes == available_nodes:
            node_rows = all_node_rows
        else:
            selected = sorted(selector.sample(range(available_nodes), requested_nodes))
            node_rows = [all_node_rows[index] for index in selected]
        node_ids = [str(row[0]) for row in node_rows]

        degree_samples = [
            int(row[0])
            for row in connection.execute(
                """
                SELECT timeseries.degree
                FROM read_parquet(?) AS timeseries
                JOIN read_parquet(?) AS profiles USING (source)
                WHERE profiles.behavior_cohort = 'bounded'
                  AND timeseries.degree BETWEEN 2 AND ?
                """,
                [str(timeseries), str(profiles), requested_nodes - 1],
            ).fetchall()
        ]
        bounded_observers = int(
            connection.execute(
                """
                SELECT count(*) FROM read_parquet(?)
                WHERE behavior_cohort = 'bounded'
                """,
                [str(profiles)],
            ).fetchone()[0]
        )
        if bounded_observers < 2:
            raise RuntimeError("fewer than two bounded observers are available")

        model_id = _model_id(
            layout, run_dir, requested_nodes, seed, swaps_per_edge
        )
        model_dir = layout.path / "models" / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        existing_manifest = model_dir / "model.json"
        if existing_manifest.exists():
            required = (
                model_dir / "full_size_nodes.parquet",
                model_dir / "full_size_snapshot.parquet",
                model_dir / "churn_model.json",
                model_dir / "churn_samples.parquet",
                model_dir / "validation_report.json",
            )
            if all(path.exists() for path in required):
                return model_dir

        degrees, graph_edges, generation = _generate_graph(
            degree_samples,
            requested_nodes,
            seed,
            swaps_per_edge,
        )

        connection.execute("CREATE TEMP TABLE selected_nodes(node_id VARCHAR)")
        connection.executemany(
            "INSERT INTO selected_nodes VALUES (?)", [(node_id,) for node_id in node_ids]
        )
        node_output = model_dir / "full_size_nodes.parquet"
        node_temporary = node_output.with_name(f".{node_output.name}.tmp-{os.getpid()}")
        if not node_output.exists():
            connection.execute(
                f"""
                COPY (
                    SELECT candidate.*, true AS synthetic_graph_member,
                           {sql_string(model_id)}::VARCHAR AS model_id
                    FROM read_parquet({sql_string(candidate_nodes)}) AS candidate
                    JOIN selected_nodes USING (node_id)
                    WHERE candidate.node_role = 'remote'
                    ORDER BY candidate.node_id
                ) TO {sql_string(node_temporary)}
                (FORMAT PARQUET, COMPRESSION ZSTD)
                """
            )
            node_temporary.replace(node_output)
        _write_edges(connection, model_dir, model_id, node_ids, graph_edges)

        client_distribution = _distribution(
            connection,
            """
            SELECT coalesce(client, '<unknown>'), count(*)
            FROM read_parquet(?)
            GROUP BY 1 ORDER BY 2 DESC, 1
            """,
            [str(node_output)],
            requested_nodes,
        )
        country_distribution = _distribution(
            connection,
            """
            SELECT coalesce(country_code, '<unknown>'), count(*)
            FROM read_parquet(?)
            GROUP BY 1 ORDER BY 2 DESC, 1
            """,
            [str(node_output)],
            requested_nodes,
        )
        asn_distribution = _distribution(
            connection,
            """
            SELECT coalesce(CAST(asn AS VARCHAR), '<unknown>'), count(*)
            FROM read_parquet(?)
            GROUP BY 1 ORDER BY 2 DESC, 1
            LIMIT 100
            """,
            [str(node_output)],
            requested_nodes,
        )
        outbound_fractions = [
            float(row[0])
            for row in connection.execute(
                """
                SELECT timeseries.outbound_degree / greatest(1.0, timeseries.degree)
                FROM read_parquet(?) AS timeseries
                JOIN read_parquet(?) AS profiles USING (source)
                WHERE profiles.behavior_cohort = 'bounded'
                """,
                [str(timeseries), str(profiles)],
            ).fetchall()
        ]
        latency_values = [
            float(row[0])
            for row in connection.execute(
                """
                SELECT edges.mean_latency_ms
                FROM read_parquet(?) AS edges
                JOIN read_parquet(?) AS profiles ON edges.source = profiles.source
                WHERE profiles.behavior_cohort = 'bounded'
                  AND edges.mean_latency_ms IS NOT NULL
                USING SAMPLE reservoir(100000 ROWS) REPEATABLE (1337)
                """,
                [str(snapshot_edges), str(profiles)],
            ).fetchall()
        ]
        visibility_rows = connection.execute(
            """
            SELECT count(DISTINCT edges.source)::DOUBLE AS observer_visibility
            FROM read_parquet(?) AS edges
            JOIN read_parquet(?) AS profiles ON edges.source = profiles.source
            WHERE profiles.behavior_cohort = 'bounded'
            GROUP BY edges.target
            """,
            [str(snapshot_edges), str(profiles)],
        ).fetchall()
        visibility = [float(row[0]) for row in visibility_rows]

        churn_row = connection.execute(
            """
            WITH selected AS (
                SELECT sessions.*
                FROM read_parquet(?) AS sessions
                JOIN read_parquet(?) AS profiles USING (source)
                WHERE profiles.behavior_cohort = 'bounded'
                  AND sessions.likely_stable
            ), spans AS (
                SELECT source,
                       date_diff('millisecond', min(first_heartbeat), max(last_heartbeat))
                           AS observed_span_ms
                FROM selected GROUP BY source
            )
            SELECT
                count(*) AS sessions,
                count(*) FILTER (WHERE NOT left_censored) AS observed_starts,
                count(*) FILTER (WHERE NOT right_censored) AS observed_ends,
                count(*) FILTER (WHERE NOT left_censored AND NOT right_censored)
                    AS complete_sessions,
                quantile_cont(observed_duration_ms, 0.05)
                    FILTER (WHERE NOT left_censored AND NOT right_censored),
                quantile_cont(observed_duration_ms, 0.50)
                    FILTER (WHERE NOT left_censored AND NOT right_censored),
                quantile_cont(observed_duration_ms, 0.95)
                    FILTER (WHERE NOT left_censored AND NOT right_censored),
                (SELECT sum(observed_span_ms) FROM spans) AS observer_span_ms
            FROM selected
            """,
            [str(sessions), str(profiles)],
        ).fetchone()
        churn_samples = model_dir / "churn_samples.parquet"
        churn_samples_temporary = churn_samples.with_name(
            f".{churn_samples.name}.tmp-{os.getpid()}"
        )
        if not churn_samples.exists():
            connection.execute(
                f"""
                COPY (
                    SELECT
                        sessions.observed_duration_ms,
                        sessions.heartbeat_count,
                        sessions.last_direction,
                        sessions.mean_latency_ms,
                        sessions.max_connection_age_ms
                    FROM read_parquet({sql_string(sessions)}) AS sessions
                    JOIN read_parquet({sql_string(profiles)}) AS profiles USING (source)
                    WHERE profiles.behavior_cohort = 'bounded'
                      AND sessions.likely_stable
                      AND NOT sessions.left_censored
                      AND NOT sessions.right_censored
                ) TO {sql_string(churn_samples_temporary)}
                (FORMAT PARQUET, COMPRESSION ZSTD)
                """
            )
            churn_samples_temporary.replace(churn_samples)
        churn_sample_size = int(
            connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(churn_samples)]
            ).fetchone()[0]
        )
    finally:
        connection.close()

    generated_degree = Counter()
    for left, right in graph_edges:
        generated_degree[left] += 1
        generated_degree[right] += 1
    generated_degrees = [generated_degree[index] for index in range(requested_nodes)]
    components = _components(requested_nodes, graph_edges)
    degree_error = [
        abs(generated_degrees[index] - degrees[index])
        for index in range(requested_nodes)
    ]
    observer_span_hours = float(churn_row[7] or 0) / 3_600_000.0
    observed_starts = int(churn_row[1] or 0)
    churn_model = {
        "schema_version": 1,
        "model_id": model_id,
        "generated_at": utc_now(),
        "fit_cohort": "bounded",
        "session_source": "heartbeat gap and connection-age-reset inference",
        "session_gap_seconds": read_json(
            snapshot_dir / "snapshot_manifest.json"
        )["parameters"]["session_gap_seconds"],
        "stable_session_min_heartbeats": 3,
        "sessions": int(churn_row[0] or 0),
        "observed_starts": observed_starts,
        "observed_ends": int(churn_row[2] or 0),
        "complete_sessions": int(churn_row[3] or 0),
        "complete_duration_ms": {
            "p05": churn_row[4],
            "p50": churn_row[5],
            "p95": churn_row[6],
            "empirical_sample_size": churn_sample_size,
        },
        "starts_per_observer_hour": (
            observed_starts / observer_span_hours if observer_span_hours else None
        ),
        "replacement_policy": "restore sampled target degree at the next model bucket",
        "enable_in_simulation_by_default": False,
        "readiness": (
            "requires multi-day held-out validation because the fitted Tysm churn "
            "rate may reflect measurement-client policy"
        ),
        "event_interval_role": "cross-validation-only",
        "limitations": [
            "The current fit uses one public Xatu day and is provisional.",
            "Durations are right/left censored at observer data boundaries.",
            "Tysm measurement behavior may differ from ordinary Beacon clients.",
        ],
    }
    atomic_write_json(model_dir / "churn_model.json", churn_model)

    model = {
        "schema_version": 1,
        "model_id": model_id,
        "algorithm": MODEL_ALGORITHM,
        "generated_at": utc_now(),
        "dataset_id": layout.dataset_id,
        "source_run_id": run_dir.name,
        "semantics": (
            "A full-size synthetic remote-peer graph calibrated from Xatu ego-network "
            "marginals; edges are not reconstructed Ethereum mainnet ground truth."
        ),
        "parameters": {
            "seed": seed,
            "node_count": requested_nodes,
            "available_remote_peers": available_nodes,
            "fit_cohort": "bounded",
            "bounded_observer_count": bounded_observers,
            "swaps_per_edge": swaps_per_edge,
        },
        "learned": {
            "active_degree": {
                "samples": len(degree_samples),
                "quantiles": _quantiles([float(value) for value in degree_samples]),
                "minimum": min(degree_samples),
                "maximum": max(degree_samples),
            },
            "remote_visibility": {
                "samples": len(visibility),
                "quantiles": _quantiles(visibility),
            },
            "client_distribution": client_distribution,
            "country_distribution": country_distribution,
            "asn_distribution_top100": asn_distribution,
            "outbound_fraction": {
                "samples": len(outbound_fractions),
                "quantiles": _quantiles(outbound_fractions),
            },
            "latency_ms": {
                "samples": len(latency_values),
                "quantiles": _quantiles(latency_values),
            },
            "edge_mixing": (
                "unconstrained: remote-to-remote mixing is not identifiable from "
                "the public observer ego graph"
            ),
        },
        "generation": generation,
        "outputs": [
            "full_size_nodes.parquet",
            "full_size_snapshot.parquet",
            "churn_model.json",
            "churn_samples.parquet",
            "validation_report.json",
        ],
    }
    atomic_write_json(model_dir / "model.json", model)

    validation = {
        "schema_version": 1,
        "model_id": model_id,
        "generated_at": utc_now(),
        "valid": (
            len(components) == 1
            and len(graph_edges) > 0
            and len(generated_degrees) == requested_nodes
        ),
        "scientific_validation_status": "provisional-single-day",
        "static_graph_ready": True,
        "churn_ready": False,
        "graph": {
            "nodes": requested_nodes,
            "edges": len(graph_edges),
            "components": len(components),
            "largest_component_nodes": len(components[0]) if components else 0,
            "self_loops": sum(left == right for left, right in graph_edges),
            "duplicate_edges": len(graph_edges) - len(set(graph_edges)),
            "target_degree_quantiles": _quantiles(
                [float(value) for value in degrees]
            ),
            "generated_degree_quantiles": _quantiles(
                [float(value) for value in generated_degrees]
            ),
            "mean_absolute_degree_error": (
                sum(degree_error) / len(degree_error) if degree_error else None
            ),
            "maximum_degree_error": max(degree_error, default=0),
        },
        "required_next_validation": (
            "Fit on multiple days and evaluate degree, edge survival and churn on a "
            "held-out day before using this as a paper result."
        ),
    }
    atomic_write_json(model_dir / "validation_report.json", validation)
    if not validation["valid"]:
        raise RuntimeError(f"generated model failed validation: {model_dir}")
    return model_dir
