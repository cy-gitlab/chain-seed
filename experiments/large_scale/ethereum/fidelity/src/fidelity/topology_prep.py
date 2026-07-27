from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .normalize import _columns, require_duckdb, sql_string
from .storage import DatasetLayout, atomic_write_json, read_json, utc_now
from .xatu import raw_file


PREP_ALGORITHM = "xatu-topology-input-v1"
REQUIRED_OUTPUTS = (
    "candidate_nodes.parquet",
    "reconstruction_manifest.json",
    "snapshots/snapshot_edges.parquet",
    "snapshots/snapshot_summary.parquet",
    "snapshots/heartbeat_sessions.parquet",
    "snapshots/observer_profiles.parquet",
    "snapshots/data_quality_report.json",
)


def _copy_parquet(connection: Any, query: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    connection.execute(
        f"COPY ({query}) TO {sql_string(temporary)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporary.replace(destination)


def _optional_cast(columns: dict[str, str], name: str, data_type: str) -> str:
    if name not in columns:
        return f"NULL::{data_type}"
    return f'CAST("{name}" AS {data_type})'


def _prep_id(layout: DatasetLayout, bucket_seconds: int, session_gap_seconds: int) -> str:
    manifest = read_json(layout.manifest_path)
    heartbeat = manifest["raw_files"]["libp2p_synthetic_heartbeat"]
    payload = {
        "algorithm": PREP_ALGORITHM,
        "heartbeat_sha256": heartbeat["sha256"],
        "bucket_seconds": bucket_seconds,
        "session_gap_seconds": session_gap_seconds,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    return f"{PREP_ALGORITHM}-{digest}"


def latest_topology_input(layout: DatasetLayout) -> Path:
    candidates = sorted(
        path
        for path in layout.derived.glob(f"{PREP_ALGORITHM}-*")
        if all((path / relative).exists() for relative in REQUIRED_OUTPUTS)
    )
    if not candidates:
        raise RuntimeError(
            f"no lightweight topology input exists for {layout.dataset_id}; "
            "run prepare-topology-day first"
        )
    return candidates[-1]


def prepare_topology_day(
    layout: DatasetLayout,
    *,
    bucket_seconds: int = 60,
    session_gap_seconds: int = 180,
) -> Path:
    if bucket_seconds < 1:
        raise ValueError("bucket_seconds must be at least 1")
    if session_gap_seconds < bucket_seconds:
        raise ValueError("session_gap_seconds must be at least bucket_seconds")
    manifest = read_json(layout.manifest_path)
    if "libp2p_synthetic_heartbeat" not in manifest.get("raw_files", {}):
        raise RuntimeError("dataset has no Xatu synthetic heartbeat table")

    source_path = raw_file(layout, "libp2p_synthetic_heartbeat")
    run_id = _prep_id(layout, bucket_seconds, session_gap_seconds)
    output_dir = layout.derived / run_id
    if all((output_dir / relative).exists() for relative in REQUIRED_OUTPUTS):
        return output_dir

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{run_id}.tmp-", dir=output_dir.parent)
    )
    snapshot_dir = temporary_dir / "snapshots"
    snapshot_dir.mkdir(parents=True)
    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        temp_storage = temporary_dir / ".duckdb-tmp"
        temp_storage.mkdir()
        connection.execute("SET TimeZone='UTC'")
        connection.execute("SET preserve_insertion_order=false")
        connection.execute(f"SET temp_directory={sql_string(temp_storage)}")
        columns = _columns(connection, source_path)
        connection.execute(
            f"""
            CREATE TEMP VIEW heartbeat AS
            SELECT
                CAST(event_date_time AS TIMESTAMP) AS event_time,
                concat('observer:', CAST(meta_client_name AS VARCHAR)) AS source,
                concat('peer:', CAST(remote_peer_id_unique_key AS VARCHAR)) AS target,
                lower(CAST(direction AS VARCHAR)) AS direction,
                CAST(connection_age_ms AS BIGINT) AS connection_age_ms,
                CAST(latency_ms AS BIGINT) AS latency_ms,
                CAST(remote_agent_implementation AS VARCHAR) AS remote_client,
                CAST(remote_agent_version AS VARCHAR) AS remote_client_version,
                CAST(remote_geo_autonomous_system_number AS VARCHAR) AS remote_asn,
                {_optional_cast(columns, 'remote_geo_autonomous_system_organization', 'VARCHAR')} AS remote_as_org,
                CAST(remote_geo_country_code AS VARCHAR) AS remote_country,
                {_optional_cast(columns, 'remote_geo_city', 'VARCHAR')} AS remote_city,
                CAST(meta_client_implementation AS VARCHAR) AS observer_client,
                CAST(meta_client_version AS VARCHAR) AS observer_client_version,
                CAST(meta_client_geo_autonomous_system_number AS VARCHAR) AS observer_asn,
                {_optional_cast(columns, 'meta_client_geo_autonomous_system_organization', 'VARCHAR')} AS observer_as_org,
                CAST(meta_client_geo_country_code AS VARCHAR) AS observer_country,
                {_optional_cast(columns, 'meta_client_geo_city', 'VARCHAR')} AS observer_city
            FROM read_parquet({sql_string(source_path)})
            WHERE event_date_time IS NOT NULL
              AND meta_client_name IS NOT NULL
              AND remote_peer_id_unique_key IS NOT NULL
            """
        )
        bucket = (
            "CAST(to_timestamp(floor(epoch(event_time) / "
            f"{bucket_seconds}) * {bucket_seconds}) AS TIMESTAMP)"
        )
        snapshot_edges = snapshot_dir / "snapshot_edges.parquet"
        _copy_parquet(
            connection,
            f"""
            SELECT
                {bucket} AS bucket_start,
                source,
                target,
                min(event_time) AS first_heartbeat,
                max(event_time) AS last_heartbeat,
                count(*)::UBIGINT AS heartbeat_count,
                arg_max(direction, event_time) AS last_direction,
                arg_max(connection_age_ms, event_time) AS connection_age_ms,
                avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS mean_latency_ms,
                arg_max(remote_client, event_time) AS remote_client,
                arg_max(remote_client_version, event_time) AS remote_client_version,
                arg_max(remote_asn, event_time) AS remote_asn,
                arg_max(remote_country, event_time) AS remote_country_code
            FROM heartbeat
            GROUP BY bucket_start, source, target
            """,
            snapshot_edges,
        )
        edges_sql = f"read_parquet({sql_string(snapshot_edges)})"
        _copy_parquet(
            connection,
            f"""
            SELECT
                bucket_start,
                count(DISTINCT source)::UBIGINT AS observer_count,
                count(DISTINCT target)::UBIGINT AS remote_peer_count,
                count(*)::UBIGINT AS edge_count,
                count(*) FILTER (WHERE last_direction='inbound')::UBIGINT AS inbound_edges,
                count(*) FILTER (WHERE last_direction='outbound')::UBIGINT AS outbound_edges
            FROM {edges_sql}
            GROUP BY bucket_start
            ORDER BY bucket_start
            """,
            snapshot_dir / "snapshot_summary.parquet",
        )
        gap_ms = session_gap_seconds * 1000
        _copy_parquet(
            connection,
            f"""
            WITH ordered AS (
                SELECT
                    source,
                    target,
                    event_time,
                    direction,
                    connection_age_ms,
                    latency_ms,
                    min(event_time) OVER (PARTITION BY source) AS observer_first,
                    max(event_time) OVER (PARTITION BY source) AS observer_last,
                    lag(event_time) OVER (
                        PARTITION BY source, target ORDER BY event_time
                    ) AS previous_time,
                    lag(connection_age_ms) OVER (
                        PARTITION BY source, target ORDER BY event_time
                    ) AS previous_age_ms
                FROM heartbeat
            ), boundaries AS (
                SELECT *,
                    CASE
                        WHEN previous_time IS NULL THEN 1
                        WHEN connection_age_ms IS NOT NULL
                         AND previous_age_ms IS NOT NULL
                         AND connection_age_ms + {bucket_seconds * 1000}
                             < previous_age_ms
                               + date_diff('millisecond', previous_time, event_time)
                            THEN 1
                        WHEN (connection_age_ms IS NULL OR previous_age_ms IS NULL)
                         AND date_diff('millisecond', previous_time, event_time) > {gap_ms}
                            THEN 1
                        ELSE 0
                    END AS starts_session
                FROM ordered
            ), labeled AS (
                SELECT *,
                    sum(starts_session) OVER (
                        PARTITION BY source, target ORDER BY event_time
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    )::UBIGINT AS session_index
                FROM boundaries
            ), sessions AS (
                SELECT
                    source,
                    target,
                    session_index,
                    min(event_time) AS first_heartbeat,
                    max(event_time) AS last_heartbeat,
                    min(observer_first) AS observer_first,
                    max(observer_last) AS observer_last,
                    count(*)::UBIGINT AS heartbeat_count,
                    arg_min(connection_age_ms, event_time) AS first_connection_age_ms,
                    arg_max(connection_age_ms, event_time) AS last_connection_age_ms,
                    max(connection_age_ms) AS max_connection_age_ms,
                    avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS mean_latency_ms,
                    arg_max(direction, event_time) AS last_direction
                FROM labeled
                GROUP BY source, target, session_index
            )
            SELECT
                source,
                target,
                session_index,
                first_heartbeat,
                last_heartbeat,
                date_diff('millisecond', first_heartbeat, last_heartbeat)
                    AS observed_duration_ms,
                heartbeat_count,
                first_connection_age_ms,
                last_connection_age_ms,
                max_connection_age_ms,
                mean_latency_ms,
                last_direction,
                coalesce(first_connection_age_ms > {gap_ms}, false) AS left_censored,
                last_heartbeat >= observer_last - INTERVAL '{session_gap_seconds} seconds'
                    AS right_censored,
                heartbeat_count >= 3
                    AND date_diff('millisecond', first_heartbeat, last_heartbeat)
                        >= {bucket_seconds * 2 * 1000} AS likely_stable
            FROM sessions
            ORDER BY source, target, first_heartbeat
            """,
            snapshot_dir / "heartbeat_sessions.parquet",
        )
        _copy_parquet(
            connection,
            f"""
            SELECT
                bucket_start,
                source,
                count(*)::UBIGINT AS degree,
                count(*) FILTER (WHERE last_direction='inbound')::UBIGINT AS inbound_degree,
                count(*) FILTER (WHERE last_direction='outbound')::UBIGINT AS outbound_degree
            FROM {edges_sql}
            GROUP BY bucket_start, source
            """,
            snapshot_dir / "observer_timeseries.parquet",
        )
        timeseries = snapshot_dir / "observer_timeseries.parquet"
        quartiles = connection.execute(
            """
            WITH per_observer AS (
                SELECT source, quantile_cont(degree, 0.95) AS degree_p95
                FROM read_parquet(?) GROUP BY source
            )
            SELECT quantile_cont(degree_p95, 0.25), quantile_cont(degree_p95, 0.75)
            FROM per_observer
            """,
            [str(timeseries)],
        ).fetchone()
        q1 = float(quartiles[0] or 0.0)
        q3 = float(quartiles[1] or 0.0)
        robust_fence = max(150.0, q3 + 3.0 * (q3 - q1))
        observer_profiles = snapshot_dir / "observer_profiles.parquet"
        _copy_parquet(
            connection,
            f"""
            WITH degree_stats AS (
                SELECT
                    source,
                    count(*)::UBIGINT AS observed_buckets,
                    min(bucket_start) AS first_bucket,
                    max(bucket_start) AS last_bucket,
                    avg(degree) AS degree_mean,
                    quantile_cont(degree, 0.05) AS degree_p05,
                    quantile_cont(degree, 0.50) AS degree_p50,
                    quantile_cont(degree, 0.95) AS degree_p95,
                    max(degree)::UBIGINT AS degree_max
                FROM read_parquet({sql_string(timeseries)})
                GROUP BY source
            ), attributes AS (
                SELECT
                    source,
                    arg_max(observer_client, event_time) AS observer_client,
                    arg_max(observer_client_version, event_time) AS observer_client_version,
                    arg_max(observer_asn, event_time) AS observer_asn,
                    arg_max(observer_country, event_time) AS observer_country_code
                FROM heartbeat GROUP BY source
            )
            SELECT *,
                CASE
                    WHEN degree_p50 > {robust_fence}
                      OR degree_p95 > {robust_fence * 2.0} THEN 'bulk-probe'
                    WHEN degree_p95 > {robust_fence} THEN 'bursty-probe'
                    ELSE 'bounded'
                END AS behavior_cohort
            FROM degree_stats JOIN attributes USING (source)
            ORDER BY degree_p50 DESC, source
            """,
            observer_profiles,
        )
        _copy_parquet(
            connection,
            """
            WITH nodes AS (
                SELECT DISTINCT source AS node_id, 'observer'::VARCHAR AS node_role
                FROM heartbeat
                UNION ALL
                SELECT DISTINCT target AS node_id, 'remote'::VARCHAR AS node_role
                FROM heartbeat
            ), observer_attributes AS (
                SELECT
                    source AS node_id,
                    arg_max(observer_client, event_time) AS client,
                    arg_max(observer_client_version, event_time) AS client_version,
                    arg_max(observer_asn, event_time) AS asn,
                    arg_max(observer_as_org, event_time) AS as_org,
                    arg_max(observer_country, event_time) AS country_code,
                    arg_max(observer_city, event_time) AS city
                FROM heartbeat GROUP BY source
            ), remote_attributes AS (
                SELECT
                    target AS node_id,
                    arg_max(remote_client, event_time) AS client,
                    arg_max(remote_client_version, event_time) AS client_version,
                    arg_max(remote_asn, event_time) AS asn,
                    arg_max(remote_as_org, event_time) AS as_org,
                    arg_max(remote_country, event_time) AS country_code,
                    arg_max(remote_city, event_time) AS city
                FROM heartbeat GROUP BY target
            )
            SELECT
                nodes.node_id,
                nodes.node_role,
                coalesce(observer_attributes.client, remote_attributes.client) AS client,
                coalesce(observer_attributes.client_version, remote_attributes.client_version)
                    AS client_version,
                coalesce(observer_attributes.asn, remote_attributes.asn) AS asn,
                coalesce(observer_attributes.as_org, remote_attributes.as_org) AS as_org,
                coalesce(observer_attributes.country_code, remote_attributes.country_code)
                    AS country_code,
                coalesce(observer_attributes.city, remote_attributes.city) AS city,
                NULL::VARCHAR AS ip
            FROM nodes
            LEFT JOIN observer_attributes USING (node_id)
            LEFT JOIN remote_attributes USING (node_id)
            ORDER BY node_role, node_id
            """,
            temporary_dir / "candidate_nodes.parquet",
        )
        summary = connection.execute(
            """
            SELECT bucket_start, observer_count, edge_count
            FROM read_parquet(?)
            ORDER BY observer_count DESC, edge_count DESC, bucket_start
            LIMIT 1
            """,
            [str(snapshot_dir / "snapshot_summary.parquet")],
        ).fetchone()
        bounds = connection.execute(
            "SELECT min(bucket_start), max(bucket_start), count(*) FROM read_parquet(?)",
            [str(snapshot_dir / "snapshot_summary.parquet")],
        ).fetchone()
        cohorts = connection.execute(
            "SELECT behavior_cohort, count(*) FROM read_parquet(?) GROUP BY 1 ORDER BY 1",
            [str(observer_profiles)],
        ).fetchall()
    finally:
        connection.close()

    reconstruction = {
        "schema_version": 1,
        "run_id": run_id,
        "algorithm": PREP_ALGORITHM,
        "created_at": utc_now(),
        "snapshot": {
            "bucket_start": summary[0].isoformat(),
            "bucket_end": None,
            "observer_count": int(summary[1]),
            "edge_count_before_dedup": int(summary[2]),
            "bucket_seconds": bucket_seconds,
        },
    }
    atomic_write_json(temporary_dir / "reconstruction_manifest.json", reconstruction)
    atomic_write_json(
        snapshot_dir / "snapshot_manifest.json",
        {
            "schema_version": 1,
            "dataset_id": layout.dataset_id,
            "run_id": run_id,
            "generated_at": utc_now(),
            "parameters": {
                "bucket_seconds": bucket_seconds,
                "session_gap_seconds": session_gap_seconds,
                "algorithm": PREP_ALGORITHM,
            },
            "snapshot_bounds": {
                "first_bucket": bounds[0].isoformat(),
                "last_bucket": bounds[1].isoformat(),
                "bucket_count": int(bounds[2]),
            },
        },
    )
    atomic_write_json(
        snapshot_dir / "data_quality_report.json",
        {
            "schema_version": 1,
            "dataset_id": layout.dataset_id,
            "run_id": run_id,
            "generated_at": utc_now(),
            "classification": {
                "degree_p95_q1": q1,
                "degree_p95_q3": q3,
                "bounded_upper_fence": robust_fence,
                "recommended_fit_cohort": "bounded",
            },
            "cohorts": [
                {"behavior_cohort": row[0], "observer_count": int(row[1])}
                for row in cohorts
            ],
            "limitations": {
                "complete_overlay_observed": False,
                "observer_remote_identity_mapping": False,
            },
        },
    )
    atomic_write_json(
        temporary_dir / "topology_input_manifest.json",
        {
            "schema_version": 1,
            "algorithm": PREP_ALGORITHM,
            "dataset_id": layout.dataset_id,
            "generated_at": utc_now(),
            "source": {
                "path": str(source_path.relative_to(layout.path)),
                "sha256": manifest["raw_files"]["libp2p_synthetic_heartbeat"]["sha256"],
            },
            "parameters": {
                "bucket_seconds": bucket_seconds,
                "session_gap_seconds": session_gap_seconds,
            },
            "outputs": list(REQUIRED_OUTPUTS),
        },
    )
    if output_dir.exists():
        raise RuntimeError(f"incomplete topology input already exists: {output_dir}")
    temporary_dir.replace(output_dir)
    return output_dir
