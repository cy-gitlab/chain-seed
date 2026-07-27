from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .normalize import require_duckdb, sql_string
from .storage import DatasetLayout, atomic_write_json, read_json, utc_now


SNAPSHOT_OUTPUTS = (
    "snapshot_edges",
    "snapshot_summary",
    "observer_timeseries",
    "heartbeat_cadence",
    "heartbeat_sessions",
)


def _copy_parquet(
    connection: Any, query: str, destination: Path, overwrite: bool = False
) -> None:
    if destination.exists() and not overwrite:
        return
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    connection.execute(
        f"COPY ({query}) TO {sql_string(temporary)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporary.replace(destination)


def _count(connection: Any, path: Path) -> int:
    return int(
        connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(path)]
        ).fetchone()[0]
    )


def _snapshot_dir(run_dir: Path) -> Path:
    return run_dir / "snapshots"


def build_snapshots(
    layout: DatasetLayout,
    run_dir: Path,
    bucket_seconds: int = 60,
    session_gap_seconds: int = 180,
) -> Path:
    if bucket_seconds < 1:
        raise ValueError("bucket_seconds must be at least 1")
    if session_gap_seconds < bucket_seconds:
        raise ValueError("session_gap_seconds must be at least bucket_seconds")

    observations = layout.normalized / "normalized_observations.parquet"
    if not observations.exists():
        raise RuntimeError("normalize the dataset before generating snapshots")
    if not (run_dir / "reconstruction_manifest.json").exists():
        raise RuntimeError(f"not a reconstruction run: {run_dir}")

    output_dir = _snapshot_dir(run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "snapshot_manifest.json"
    parameters = {
        "bucket_seconds": bucket_seconds,
        "session_gap_seconds": session_gap_seconds,
        "algorithm": "xatu-heartbeat-temporal-v2",
    }
    if manifest_path.exists():
        existing = read_json(manifest_path)
        if existing.get("parameters") != parameters:
            raise RuntimeError(
                "snapshot outputs already exist with different parameters; "
                "use the recorded parameters or a different reconstruction run"
            )
        if all((output_dir / f"{name}.parquet").exists() for name in SNAPSHOT_OUTPUTS):
            return output_dir

    source = f"read_parquet({sql_string(observations)})"
    bucket = (
        "CAST(to_timestamp("
        f"floor(epoch(event_time) / {bucket_seconds}) * {bucket_seconds}"
        ") AS TIMESTAMP)"
    )
    gap_ms = session_gap_seconds * 1000
    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        temp_dir = output_dir / ".duckdb-tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute("SET preserve_insertion_order = false")
        connection.execute(f"SET temp_directory = {sql_string(temp_dir)}")

        snapshot_edges = output_dir / "snapshot_edges.parquet"
        _copy_parquet(
            connection,
            f"""
            SELECT
                {bucket} AS bucket_start,
                observer_id AS source,
                remote_peer_id AS target,
                min(event_time) AS first_heartbeat,
                max(event_time) AS last_heartbeat,
                count(*)::UBIGINT AS heartbeat_count,
                lower(arg_max(direction, event_time)) AS last_direction,
                arg_max(connection_age_ms, event_time) AS connection_age_ms,
                avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS mean_latency_ms,
                arg_max(remote_client, event_time)
                    FILTER (WHERE remote_client IS NOT NULL) AS remote_client,
                arg_max(remote_client_version, event_time)
                    FILTER (WHERE remote_client_version IS NOT NULL) AS remote_client_version,
                arg_max(remote_asn, event_time)
                    FILTER (WHERE remote_asn IS NOT NULL) AS remote_asn,
                arg_max(remote_country_code, event_time)
                    FILTER (WHERE remote_country_code IS NOT NULL) AS remote_country_code
            FROM {source}
            WHERE event_kind = 'heartbeat'
            GROUP BY bucket_start, observer_id, remote_peer_id
            """,
            snapshot_edges,
        )

        edge_source = f"read_parquet({sql_string(snapshot_edges)})"
        _copy_parquet(
            connection,
            f"""
            SELECT
                bucket_start,
                count(DISTINCT source)::UBIGINT AS observer_count,
                count(DISTINCT target)::UBIGINT AS remote_peer_count,
                count(*)::UBIGINT AS edge_count,
                count(*) FILTER (WHERE last_direction = 'inbound')::UBIGINT
                    AS inbound_edges,
                count(*) FILTER (WHERE last_direction = 'outbound')::UBIGINT
                    AS outbound_edges
            FROM {edge_source}
            GROUP BY bucket_start
            ORDER BY bucket_start
            """,
            output_dir / "snapshot_summary.parquet",
        )
        _copy_parquet(
            connection,
            f"""
            SELECT
                bucket_start,
                source,
                count(*)::UBIGINT AS degree,
                count(*) FILTER (WHERE last_direction = 'inbound')::UBIGINT
                    AS inbound_degree,
                count(*) FILTER (WHERE last_direction = 'outbound')::UBIGINT
                    AS outbound_degree,
                count(*) FILTER (WHERE last_direction NOT IN ('inbound', 'outbound'))::UBIGINT
                    AS unknown_direction_degree,
                quantile_cont(mean_latency_ms, 0.50)
                    FILTER (WHERE mean_latency_ms IS NOT NULL) AS latency_ms_p50,
                quantile_cont(connection_age_ms, 0.50)
                    FILTER (WHERE connection_age_ms IS NOT NULL) AS connection_age_ms_p50
            FROM {edge_source}
            GROUP BY bucket_start, source
            ORDER BY bucket_start, source
            """,
            output_dir / "observer_timeseries.parquet",
        )

        _copy_parquet(
            connection,
            f"""
            WITH ordered AS (
                SELECT
                    observer_id AS source,
                    event_time,
                    date_diff(
                        'millisecond',
                        lag(event_time) OVER (
                            PARTITION BY observer_id, remote_peer_id
                            ORDER BY event_time
                        ),
                        event_time
                    ) AS gap_ms
                FROM {source}
                WHERE event_kind = 'heartbeat'
            )
            SELECT
                source,
                count(*) FILTER (WHERE gap_ms > 0)::UBIGINT AS gap_samples,
                quantile_cont(gap_ms, 0.50) FILTER (WHERE gap_ms > 0) AS gap_ms_p50,
                quantile_cont(gap_ms, 0.95) FILTER (WHERE gap_ms > 0) AS gap_ms_p95,
                quantile_cont(gap_ms, 0.99) FILTER (WHERE gap_ms > 0) AS gap_ms_p99,
                count(*) FILTER (WHERE gap_ms > {gap_ms})::UBIGINT
                    AS gaps_above_session_threshold
            FROM ordered
            GROUP BY source
            ORDER BY source
            """,
            output_dir / "heartbeat_cadence.parquet",
        )

        _copy_parquet(
            connection,
            f"""
            WITH heartbeat AS (
                SELECT
                    observer_id AS source,
                    remote_peer_id AS target,
                    event_time,
                    lower(direction) AS direction,
                    connection_age_ms,
                    latency_ms,
                    min(event_time) OVER (PARTITION BY observer_id) AS observer_first,
                    max(event_time) OVER (PARTITION BY observer_id) AS observer_last,
                    lag(event_time) OVER (
                        PARTITION BY observer_id, remote_peer_id ORDER BY event_time
                    ) AS previous_time,
                    lag(connection_age_ms) OVER (
                        PARTITION BY observer_id, remote_peer_id ORDER BY event_time
                    ) AS previous_age_ms
                FROM {source}
                WHERE event_kind = 'heartbeat'
            ), boundaries AS (
                SELECT
                    *,
                    date_diff('millisecond', previous_time, event_time) AS gap_ms,
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
                FROM heartbeat
            ), labeled AS (
                SELECT
                    *,
                    sum(starts_session) OVER (
                        PARTITION BY source, target
                        ORDER BY event_time
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
                        >= {bucket_seconds * 2 * 1000}
                    AS likely_stable
            FROM sessions
            ORDER BY source, target, first_heartbeat
            """,
            output_dir / "heartbeat_sessions.parquet",
        )

        counts = {
            name: _count(connection, output_dir / f"{name}.parquet")
            for name in SNAPSHOT_OUTPUTS
        }
        bounds = connection.execute(
            """
            SELECT min(bucket_start), max(bucket_start), count(*)
            FROM read_parquet(?)
            """,
            [str(output_dir / "snapshot_summary.parquet")],
        ).fetchone()
    finally:
        connection.close()

    manifest = {
        "schema_version": 1,
        "dataset_id": layout.dataset_id,
        "run_id": run_dir.name,
        "generated_at": utc_now(),
        "parameters": parameters,
        "semantics": {
            "snapshot_edges": "deduplicated Xatu heartbeat ego-edges per time bucket",
            "heartbeat_sessions": (
                "observer-to-remote sessions inferred from heartbeat gaps and "
                "connection-age resets; not remote-to-remote ground truth"
            ),
        },
        "snapshot_bounds": {
            "first_bucket": bounds[0].isoformat() if bounds[0] else None,
            "last_bucket": bounds[1].isoformat() if bounds[1] else None,
            "bucket_count": int(bounds[2]),
        },
        "counts": counts,
        "outputs": [f"{name}.parquet" for name in SNAPSHOT_OUTPUTS],
    }
    atomic_write_json(manifest_path, manifest)
    return output_dir


def _name_family_sql(column: str = "source") -> str:
    return f"""
        CASE
            WHEN {column} LIKE '%xatu-tysm-sharded%' THEN 'sharded-utility'
            WHEN {column} LIKE '%/sigma-%' THEN 'sigma'
            WHEN {column} LIKE '%/utility-%' THEN 'utility'
            ELSE 'unclassified'
        END
    """


def audit_snapshots(layout: DatasetLayout, run_dir: Path) -> Path:
    snapshot_dir = _snapshot_dir(run_dir)
    manifest_path = snapshot_dir / "snapshot_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("generate snapshots before running the audit")
    snapshot_manifest = read_json(manifest_path)
    timeseries = snapshot_dir / "observer_timeseries.parquet"
    edges = snapshot_dir / "snapshot_edges.parquet"
    sessions = snapshot_dir / "heartbeat_sessions.parquet"
    cadence = snapshot_dir / "heartbeat_cadence.parquet"
    event_intervals = run_dir / "connection_intervals.parquet"
    observations = layout.normalized / "normalized_observations.parquet"

    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute("SET preserve_insertion_order = false")
        profile_base = f"""
            WITH observer_attributes AS (
                SELECT
                    observer_id AS source,
                    arg_max(observer_client, event_time)
                        FILTER (WHERE observer_client IS NOT NULL) AS observer_client,
                    arg_max(observer_client_version, event_time)
                        FILTER (WHERE observer_client_version IS NOT NULL)
                        AS observer_client_version,
                    arg_max(observer_asn, event_time)
                        FILTER (WHERE observer_asn IS NOT NULL) AS observer_asn,
                    arg_max(observer_country_code, event_time)
                        FILTER (WHERE observer_country_code IS NOT NULL)
                        AS observer_country_code
                FROM read_parquet({sql_string(observations)})
                GROUP BY observer_id
            ), time_stats AS (
                SELECT
                    source,
                    count(*)::UBIGINT AS observed_buckets,
                    min(bucket_start) AS first_bucket,
                    max(bucket_start) AS last_bucket,
                    avg(degree) AS degree_mean,
                    quantile_cont(degree, 0.05) AS degree_p05,
                    quantile_cont(degree, 0.50) AS degree_p50,
                    quantile_cont(degree, 0.95) AS degree_p95,
                    max(degree)::UBIGINT AS degree_max,
                    sum(inbound_degree)::UBIGINT AS inbound_edge_samples,
                    sum(outbound_degree)::UBIGINT AS outbound_edge_samples
                FROM read_parquet({sql_string(timeseries)})
                GROUP BY source
            ), peer_stats AS (
                SELECT
                    source,
                    count(DISTINCT target)::UBIGINT AS unique_remote_peers
                FROM read_parquet({sql_string(edges)})
                GROUP BY source
            ), session_stats AS (
                SELECT
                    source,
                    count(*)::UBIGINT AS heartbeat_sessions,
                    count(*) FILTER (WHERE likely_stable)::UBIGINT AS stable_sessions,
                    count(*) FILTER (WHERE left_censored)::UBIGINT AS left_censored_sessions,
                    count(*) FILTER (WHERE right_censored)::UBIGINT AS right_censored_sessions,
                    quantile_cont(observed_duration_ms, 0.50)
                        FILTER (WHERE likely_stable) AS stable_duration_ms_p50,
                    quantile_cont(observed_duration_ms, 0.95)
                        FILTER (WHERE likely_stable) AS stable_duration_ms_p95
                FROM read_parquet({sql_string(sessions)})
                GROUP BY source
            )
            SELECT
                time_stats.source,
                {_name_family_sql('time_stats.source')} AS name_family,
                observer_attributes.observer_client,
                observer_attributes.observer_client_version,
                observer_attributes.observer_asn,
                observer_attributes.observer_country_code,
                observed_buckets,
                first_bucket,
                last_bucket,
                observed_buckets / greatest(
                    1.0,
                    date_diff(
                        'second', first_bucket, last_bucket
                    ) / {snapshot_manifest['parameters']['bucket_seconds']} + 1
                ) AS within_span_availability,
                degree_mean,
                degree_p05,
                degree_p50,
                degree_p95,
                degree_max,
                inbound_edge_samples,
                outbound_edge_samples,
                outbound_edge_samples / greatest(
                    1.0, inbound_edge_samples + outbound_edge_samples
                ) AS outbound_fraction,
                peer_stats.unique_remote_peers,
                session_stats.heartbeat_sessions,
                session_stats.stable_sessions,
                session_stats.left_censored_sessions,
                session_stats.right_censored_sessions,
                session_stats.stable_duration_ms_p50,
                session_stats.stable_duration_ms_p95,
                cadence.gap_ms_p50 AS heartbeat_gap_ms_p50,
                cadence.gap_ms_p95 AS heartbeat_gap_ms_p95,
                cadence.gap_ms_p99 AS heartbeat_gap_ms_p99
            FROM time_stats
            JOIN observer_attributes USING (source)
            JOIN peer_stats USING (source)
            JOIN session_stats USING (source)
            JOIN read_parquet({sql_string(cadence)}) AS cadence USING (source)
        """
        quartiles = connection.execute(
            f"""
            SELECT
                quantile_cont(degree_p95, 0.25),
                quantile_cont(degree_p95, 0.75)
            FROM ({profile_base})
            """
        ).fetchone()
        q1 = float(quartiles[0] or 0.0)
        q3 = float(quartiles[1] or 0.0)
        robust_fence = max(150.0, q3 + 3.0 * (q3 - q1))
        profiles = snapshot_dir / "observer_profiles.parquet"
        _copy_parquet(
            connection,
            f"""
            SELECT
                *,
                CASE
                    WHEN degree_p50 > {robust_fence}
                      OR degree_p95 > {robust_fence * 2.0}
                        THEN 'bulk-probe'
                    WHEN degree_p95 > {robust_fence}
                        THEN 'bursty-probe'
                    ELSE 'bounded'
                END AS behavior_cohort
            FROM ({profile_base})
            ORDER BY degree_p50 DESC, source
            """,
            profiles,
            overwrite=True,
        )
        cohorts = snapshot_dir / "observer_cohorts.parquet"
        _copy_parquet(
            connection,
            f"""
            SELECT
                name_family,
                behavior_cohort,
                count(*)::UBIGINT AS observer_count,
                quantile_cont(degree_p50, 0.50) AS observer_degree_p50,
                quantile_cont(degree_p95, 0.50) AS observer_degree_p95,
                min(degree_p05) AS degree_min,
                max(degree_max) AS degree_max
            FROM read_parquet({sql_string(profiles)})
            GROUP BY name_family, behavior_cohort
            ORDER BY name_family, behavior_cohort
            """,
            cohorts,
            overwrite=True,
        )

        cohort_rows = connection.execute(
            """
            SELECT behavior_cohort, count(*), min(degree_p50),
                   quantile_cont(degree_p50, 0.50), max(degree_p95)
            FROM read_parquet(?)
            GROUP BY behavior_cohort
            ORDER BY behavior_cohort
            """,
            [str(profiles)],
        ).fetchall()
        heartbeat_stats = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE likely_stable),
                count(*) FILTER (WHERE left_censored),
                count(*) FILTER (WHERE right_censored),
                quantile_cont(observed_duration_ms, 0.50)
                    FILTER (WHERE likely_stable),
                quantile_cont(observed_duration_ms, 0.95)
                    FILTER (WHERE likely_stable),
                quantile_cont(max_connection_age_ms, 0.50)
                    FILTER (WHERE max_connection_age_ms IS NOT NULL)
            FROM read_parquet(?)
            """,
            [str(sessions)],
        ).fetchone()
        event_duration_p50 = connection.execute(
            """
            SELECT quantile_cont(duration_ms, 0.50)
            FROM read_parquet(?)
            WHERE NOT left_censored AND NOT right_censored AND duration_ms >= 0
            """,
            [str(event_intervals)],
        ).fetchone()[0]
        heartbeat_event_age_p50 = connection.execute(
            """
            SELECT quantile_cont(connection_age_ms, 0.50)
            FROM read_parquet(?)
            WHERE connection_age_ms IS NOT NULL
            """,
            [str(edges)],
        ).fetchone()[0]
        global_cadence = connection.execute(
            """
            SELECT quantile_cont(gap_ms_p50, 0.50),
                   quantile_cont(gap_ms_p95, 0.50)
            FROM read_parquet(?)
            """,
            [str(cadence)],
        ).fetchone()
        observer_implementations = connection.execute(
            """
            SELECT coalesce(observer_client, '<unknown>'), count(*)
            FROM read_parquet(?) GROUP BY 1 ORDER BY 2 DESC, 1
            """,
            [str(profiles)],
        ).fetchall()
    finally:
        connection.close()

    session_max_age_p50 = heartbeat_stats[6]
    warnings = [
        (
            "All topology edges are Xatu observer-to-remote ego edges; "
            "remote-to-remote adjacency is unobserved."
        ),
        (
            "Observer and remote identities remain separate namespaces because "
            "the public data provides no reliable mapping."
        ),
    ]
    if (
        event_duration_p50 is not None
        and heartbeat_event_age_p50 is not None
        and float(event_duration_p50) * 10 < float(heartbeat_event_age_p50)
    ):
        warnings.append(
            "Connected/disconnected event durations are much shorter than heartbeat "
            "connection ages; event intervals must not be used as the sole churn model."
        )

    report = {
        "schema_version": 1,
        "dataset_id": layout.dataset_id,
        "run_id": run_dir.name,
        "generated_at": utc_now(),
        "classification": {
            "degree_p95_q1": q1,
            "degree_p95_q3": q3,
            "bounded_upper_fence": robust_fence,
            "rules": {
                "bulk-probe": (
                    f"degree_p50 > {robust_fence} or "
                    f"degree_p95 > {robust_fence * 2.0}"
                ),
                "bursty-probe": f"degree_p95 > {robust_fence}",
                "bounded": "all remaining observers",
            },
            "recommended_fit_cohort": "bounded",
        },
        "cohorts": [
            {
                "behavior_cohort": row[0],
                "observer_count": int(row[1]),
                "degree_p50_min": row[2],
                "degree_p50_median": row[3],
                "degree_p95_max": row[4],
            }
            for row in cohort_rows
        ],
        "observer_implementations": [
            {"client": row[0], "observer_count": int(row[1])}
            for row in observer_implementations
        ],
        "heartbeat": {
            "sessions": int(heartbeat_stats[0]),
            "likely_stable_sessions": int(heartbeat_stats[1]),
            "left_censored_sessions": int(heartbeat_stats[2]),
            "right_censored_sessions": int(heartbeat_stats[3]),
            "stable_duration_ms_p50": heartbeat_stats[4],
            "stable_duration_ms_p95": heartbeat_stats[5],
            "event_connection_age_ms_p50": heartbeat_event_age_p50,
            "session_max_connection_age_ms_p50": session_max_age_p50,
            "observer_gap_ms_p50_median": global_cadence[0],
            "observer_gap_ms_p95_median": global_cadence[1],
        },
        "connection_events": {
            "complete_duration_ms_p50": event_duration_p50,
            "use_for_churn": "cross-validation-only",
        },
        "limitations": {
            "complete_overlay_observed": False,
            "observer_remote_identity_mapping": False,
            "ordinary_beacon_observers_confirmed": False,
        },
        "warnings": warnings,
        "outputs": ["observer_profiles.parquet", "observer_cohorts.parquet"],
    }
    report_path = snapshot_dir / "data_quality_report.json"
    atomic_write_json(report_path, report)
    return report_path
