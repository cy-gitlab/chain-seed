from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .normalize import require_duckdb, sql_string
from .storage import DatasetLayout, atomic_write_json, read_json, utc_now


DERIVED_TABLES = (
    "connection_events",
    "connection_intervals",
    "edge_history",
    "g0_edges",
    "candidate_edges",
    "replacement_edges",
    "g0_nodes",
    "candidate_nodes",
)


def _copy_parquet(connection: Any, query: str, destination: Path) -> None:
    if destination.exists():
        return
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    connection.execute(
        f"COPY ({query}) TO {sql_string(temporary)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporary.replace(destination)


def _copy_csv(connection: Any, source: Path, destination: Path) -> None:
    if destination.exists():
        return
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    connection.execute(
        f"COPY (SELECT * FROM read_parquet({sql_string(source)})) "
        f"TO {sql_string(temporary)} (HEADER, DELIMITER ',')"
    )
    temporary.replace(destination)


def _processing_run_id(layout: DatasetLayout, bucket_seconds: int) -> str:
    raw_manifest = read_json(layout.manifest_path)
    normalized_manifest = read_json(layout.normalized / "normalization_manifest.json")
    payload = {
        "raw": {
            table: entry["sha256"]
            for table, entry in sorted(raw_manifest["raw_files"].items())
        },
        "normalized_inputs": normalized_manifest["inputs"],
        "snapshot_bucket_seconds": bucket_seconds,
        "algorithm": "observed-ego-v1",
    }
    digest = hashlib.sha256(repr(payload).encode()).hexdigest()[:12]
    return f"observed-ego-v1-{digest}"


def _snapshot(connection: Any, observations: Path, bucket_seconds: int) -> dict[str, Any]:
    row = connection.execute(
        f"""
        SELECT
            CAST(to_timestamp(
                floor(epoch(event_time) / {bucket_seconds}) * {bucket_seconds}
            ) AS TIMESTAMP)
                AS bucket_start,
            count(DISTINCT observer_id) AS observer_count,
            count(DISTINCT (observer_id, remote_peer_id)) AS edge_count,
            count(*) AS heartbeat_rows
        FROM read_parquet(?)
        WHERE event_kind = 'heartbeat'
        GROUP BY bucket_start
        ORDER BY observer_count DESC, edge_count DESC, heartbeat_rows DESC, bucket_start
        LIMIT 1
        """,
        [str(observations)],
    ).fetchone()
    if row is None:
        raise RuntimeError("no heartbeat rows are available for G0 reconstruction")
    return {
        "bucket_start": row[0].isoformat(),
        "bucket_end": (
            connection.execute(
                f"SELECT CAST(? AS TIMESTAMP) + INTERVAL '{bucket_seconds} seconds'",
                [row[0]],
            ).fetchone()[0]
        ).isoformat(),
        "observer_count": int(row[1]),
        "edge_count_before_dedup": int(row[2]),
        "heartbeat_rows": int(row[3]),
        "bucket_seconds": bucket_seconds,
    }


def _edge_queries(observations: Path, snapshot: dict[str, Any]) -> dict[str, str]:
    source = f"read_parquet({sql_string(observations)})"
    start = sql_string(snapshot["bucket_start"])
    end = sql_string(snapshot["bucket_end"])
    candidate = f"""
        SELECT
            observer_id AS source,
            remote_peer_id AS target,
            min(event_time) AS first_seen,
            max(event_time) AS last_seen,
            count(*) FILTER (WHERE event_kind = 'heartbeat')::UBIGINT AS heartbeat_count,
            count(*) FILTER (WHERE event_kind = 'connected')::UBIGINT AS connected_count,
            avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS mean_latency_ms,
            arg_max(direction, event_time) FILTER (WHERE direction IS NOT NULL) AS last_direction
        FROM {source}
        WHERE event_kind IN ('heartbeat', 'connected')
        GROUP BY observer_id, remote_peer_id
    """
    g0 = f"""
        SELECT
            observer_id AS source,
            remote_peer_id AS target,
            min(event_time) AS first_observed_in_snapshot,
            max(event_time) AS last_observed_in_snapshot,
            count(*)::UBIGINT AS heartbeat_count,
            avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS mean_latency_ms,
            arg_max(direction, event_time) FILTER (WHERE direction IS NOT NULL) AS last_direction
        FROM {source}
        WHERE event_kind = 'heartbeat'
          AND event_time >= CAST({start} AS TIMESTAMP)
          AND event_time < CAST({end} AS TIMESTAMP)
        GROUP BY observer_id, remote_peer_id
    """
    return {"candidate": candidate, "g0": g0}


def _node_query(edges: Path, observations: Path) -> str:
    edge_source = f"read_parquet({sql_string(edges)})"
    observation_source = f"read_parquet({sql_string(observations)})"
    return f"""
        WITH edge_nodes AS (
            SELECT source AS node_id, 'observer'::VARCHAR AS node_role FROM {edge_source}
            UNION
            SELECT target AS node_id, 'remote'::VARCHAR AS node_role FROM {edge_source}
        ),
        observer_attributes AS (
            SELECT
                observer_id AS node_id,
                arg_max(observer_client, event_time) FILTER (WHERE observer_client IS NOT NULL) AS client,
                arg_max(observer_client_version, event_time) FILTER (WHERE observer_client_version IS NOT NULL) AS client_version,
                arg_max(observer_asn, event_time) FILTER (WHERE observer_asn IS NOT NULL) AS asn,
                arg_max(observer_as_org, event_time) FILTER (WHERE observer_as_org IS NOT NULL) AS as_org,
                arg_max(observer_country_code, event_time) FILTER (WHERE observer_country_code IS NOT NULL) AS country_code,
                arg_max(observer_city, event_time) FILTER (WHERE observer_city IS NOT NULL) AS city,
                arg_max(observer_ip, event_time) FILTER (WHERE observer_ip IS NOT NULL) AS ip
            FROM {observation_source}
            GROUP BY observer_id
        ),
        remote_attributes AS (
            SELECT
                remote_peer_id AS node_id,
                arg_max(remote_client, event_time) FILTER (WHERE remote_client IS NOT NULL) AS client,
                arg_max(remote_client_version, event_time) FILTER (WHERE remote_client_version IS NOT NULL) AS client_version,
                arg_max(remote_asn, event_time) FILTER (WHERE remote_asn IS NOT NULL) AS asn,
                arg_max(remote_as_org, event_time) FILTER (WHERE remote_as_org IS NOT NULL) AS as_org,
                arg_max(remote_country_code, event_time) FILTER (WHERE remote_country_code IS NOT NULL) AS country_code,
                arg_max(remote_city, event_time) FILTER (WHERE remote_city IS NOT NULL) AS city,
                arg_max(remote_ip, event_time) FILTER (WHERE remote_ip IS NOT NULL) AS ip
            FROM {observation_source}
            GROUP BY remote_peer_id
        )
        SELECT
            edge_nodes.node_id,
            edge_nodes.node_role,
            coalesce(observer_attributes.client, remote_attributes.client) AS client,
            coalesce(observer_attributes.client_version, remote_attributes.client_version) AS client_version,
            coalesce(observer_attributes.asn, remote_attributes.asn) AS asn,
            coalesce(observer_attributes.as_org, remote_attributes.as_org) AS as_org,
            coalesce(observer_attributes.country_code, remote_attributes.country_code) AS country_code,
            coalesce(observer_attributes.city, remote_attributes.city) AS city,
            coalesce(observer_attributes.ip, remote_attributes.ip) AS ip
        FROM edge_nodes
        LEFT JOIN observer_attributes USING (node_id)
        LEFT JOIN remote_attributes USING (node_id)
        ORDER BY edge_nodes.node_id
    """


def reconstruct_dataset(
    layout: DatasetLayout,
    bucket_seconds: int,
    export_csv: bool = True,
) -> Path:
    if bucket_seconds < 1:
        raise ValueError("bucket_seconds must be at least 1")
    observations = layout.normalized / "normalized_observations.parquet"
    if not observations.exists():
        raise RuntimeError("normalize the dataset before reconstruction")

    run_id = _processing_run_id(layout, bucket_seconds)
    run_dir = layout.derived / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        (run_dir / ".duckdb-tmp").mkdir(parents=True, exist_ok=True)
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute("SET preserve_insertion_order = false")
        connection.execute(f"SET temp_directory = {sql_string(run_dir / '.duckdb-tmp')}")
        snapshot = _snapshot(connection, observations, bucket_seconds)
        queries = _edge_queries(observations, snapshot)

        g0_edges = run_dir / "g0_edges.parquet"
        candidate_edges = run_dir / "candidate_edges.parquet"
        replacement_edges = run_dir / "replacement_edges.parquet"
        _copy_parquet(connection, queries["g0"], g0_edges)
        _copy_parquet(connection, queries["candidate"], candidate_edges)
        _copy_parquet(
            connection,
            f"""
            SELECT candidate.*
            FROM read_parquet({sql_string(candidate_edges)}) AS candidate
            ANTI JOIN read_parquet({sql_string(g0_edges)}) AS initial
              ON candidate.source = initial.source AND candidate.target = initial.target
            """,
            replacement_edges,
        )

        connection_events = run_dir / "connection_events.parquet"
        _copy_parquet(
            connection,
            f"""
            SELECT *
            FROM read_parquet({sql_string(observations)})
            WHERE event_kind IN ('connected', 'disconnected')
            ORDER BY observer_id, remote_peer_id, event_time
            """,
            connection_events,
        )
        connection_intervals = run_dir / "connection_intervals.parquet"
        _copy_parquet(
            connection,
            f"""
            WITH events AS (
                SELECT
                    *,
                    coalesce(opened_at, event_time) AS session_key
                FROM read_parquet({sql_string(connection_events)})
            )
            SELECT
                observer_id AS source,
                remote_peer_id AS target,
                session_key,
                coalesce(
                    min(event_time) FILTER (WHERE event_kind = 'connected'),
                    min(opened_at),
                    min(event_time)
                ) AS start_time,
                max(event_time) FILTER (WHERE event_kind = 'disconnected') AS end_time,
                count(*) FILTER (WHERE event_kind = 'connected') = 0 AS left_censored,
                count(*) FILTER (WHERE event_kind = 'disconnected') = 0 AS right_censored,
                CASE
                    WHEN count(*) FILTER (WHERE event_kind = 'disconnected') > 0
                    THEN date_diff(
                        'millisecond',
                        coalesce(
                            min(event_time) FILTER (WHERE event_kind = 'connected'),
                            min(opened_at),
                            min(event_time)
                        ),
                        max(event_time) FILTER (WHERE event_kind = 'disconnected')
                    )
                    ELSE NULL
                END AS duration_ms,
                count(*)::UBIGINT AS source_event_count,
                arg_max(direction, event_time) FILTER (WHERE direction IS NOT NULL) AS last_direction
            FROM events
            GROUP BY observer_id, remote_peer_id, session_key
            ORDER BY source, target, start_time
            """,
            connection_intervals,
        )

        edge_history = run_dir / "edge_history.parquet"
        _copy_parquet(
            connection,
            f"""
            WITH observations AS (
                SELECT * FROM read_parquet({sql_string(observations)})
                WHERE event_kind IN ('heartbeat', 'connected', 'disconnected')
            ),
            initial AS (
                SELECT source, target FROM read_parquet({sql_string(g0_edges)})
            ),
            candidates AS (
                SELECT source, target FROM read_parquet({sql_string(candidate_edges)})
            )
            SELECT
                observations.observer_id AS source,
                observations.remote_peer_id AS target,
                min(observations.event_time) AS first_seen,
                max(observations.event_time) AS last_seen,
                count(*) FILTER (WHERE event_kind = 'heartbeat')::UBIGINT AS heartbeat_count,
                count(*) FILTER (WHERE event_kind = 'connected')::UBIGINT AS connected_count,
                count(*) FILTER (WHERE event_kind = 'disconnected')::UBIGINT AS disconnected_count,
                max(connection_age_ms) AS max_connection_age_ms,
                avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS mean_latency_ms,
                initial.source IS NOT NULL AS active_in_g0,
                candidates.source IS NOT NULL AS included_in_c
            FROM observations
            LEFT JOIN initial
              ON observations.observer_id = initial.source
             AND observations.remote_peer_id = initial.target
            LEFT JOIN candidates
              ON observations.observer_id = candidates.source
             AND observations.remote_peer_id = candidates.target
            GROUP BY observations.observer_id, observations.remote_peer_id,
                     initial.source, candidates.source
            ORDER BY source, target
            """,
            edge_history,
        )

        g0_nodes = run_dir / "g0_nodes.parquet"
        candidate_nodes = run_dir / "candidate_nodes.parquet"
        _copy_parquet(connection, _node_query(g0_edges, observations), g0_nodes)
        _copy_parquet(connection, _node_query(candidate_edges, observations), candidate_nodes)

        counts = {}
        for name in DERIVED_TABLES:
            path = run_dir / f"{name}.parquet"
            counts[name] = int(
                connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [str(path)]
                ).fetchone()[0]
            )
            if export_csv:
                _copy_csv(connection, path, run_dir / f"{name}.csv")

        missing = connection.execute(
            f"""
            SELECT count(*)
            FROM read_parquet({sql_string(g0_edges)}) AS initial
            ANTI JOIN read_parquet({sql_string(candidate_edges)}) AS candidate
              ON initial.source = candidate.source AND initial.target = candidate.target
            """
        ).fetchone()[0]
        if missing:
            raise RuntimeError(f"G0 is not a subset of C: {missing} missing edges")
    finally:
        connection.close()

    run_manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "algorithm": "observed-ego-v1",
        "created_at": utc_now(),
        "snapshot": snapshot,
        "semantics": {
            "g0": "heartbeat edges active in the selected snapshot bucket",
            "c": "union of heartbeat and connected edges in the observation window",
            "c_new": "C minus G0",
            "disconnect_only": "retained in events/intervals but does not create a C edge",
        },
        "counts": counts,
        "outputs": [f"{name}.parquet" for name in DERIVED_TABLES],
    }
    run_manifest_path = run_dir / "reconstruction_manifest.json"
    if run_manifest_path.exists():
        existing = read_json(run_manifest_path)
        if existing.get("snapshot") != snapshot or existing.get("counts") != counts:
            raise RuntimeError("existing reconstruction run differs from deterministic output")
    else:
        atomic_write_json(run_manifest_path, run_manifest)

    dataset_manifest = read_json(layout.manifest_path)
    dataset_manifest.setdefault("processing_runs", {})[run_id] = {
        "path": str(run_dir.relative_to(layout.path)),
        "created_at": run_manifest["created_at"],
        "snapshot": snapshot,
    }
    atomic_write_json(layout.manifest_path, dataset_manifest)
    return run_dir


def latest_run(layout: DatasetLayout) -> Path:
    manifest = read_json(layout.manifest_path)
    runs = manifest.get("processing_runs", {})
    if not runs:
        raise RuntimeError("no reconstruction run exists")
    run_id = max(runs, key=lambda key: runs[key]["created_at"])
    return layout.path / runs[run_id]["path"]
