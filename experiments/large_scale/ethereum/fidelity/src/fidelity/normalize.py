from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .storage import DatasetLayout, atomic_write_json, read_json, utc_now
from .xatu import raw_file


TABLE_EVENT_KIND = {
    "libp2p_synthetic_heartbeat": "heartbeat",
    "libp2p_connected": "connected",
    "libp2p_disconnected": "disconnected",
    "libp2p_identify": "identify",
}


def require_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as error:
        raise RuntimeError(
            "duckdb is required; install fidelity/requirements.txt in a virtualenv"
        ) from error
    return duckdb


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _columns(connection: Any, path: Path) -> dict[str, str]:
    rows = connection.execute(
        "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _field(
    columns: dict[str, str],
    name: str,
    output: str,
    data_type: str,
    transform: str | None = None,
) -> str:
    if name in columns:
        expression = transform.format(column=f'"{name}"') if transform else f'"{name}"'
        return f"CAST({expression} AS {data_type}) AS {output}"
    return f"NULL::{data_type} AS {output}"


def _timestamp_field(columns: dict[str, str], name: str, output: str) -> str:
    if name not in columns:
        return f"NULL::TIMESTAMP AS {output}"

    column = f'"{name}"'
    source_type = columns[name].upper()
    integer_types = {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
    }
    if source_type in integer_types:
        expression = f"to_timestamp(CAST({column} AS DOUBLE))"
    else:
        expression = column
    return f"CAST({expression} AS TIMESTAMP) AS {output}"


def _normalized_select(
    table: str, path: Path, columns: dict[str, str]
) -> str:
    event_kind = TABLE_EVENT_KIND[table]
    required = {"event_date_time", "meta_client_name", "remote_peer_id_unique_key"}
    missing = required - set(columns)
    if missing:
        raise RuntimeError(f"{table} is missing required columns: {sorted(missing)}")

    fields = [
        f"{sql_string(table)}::VARCHAR AS source_table",
        f"{sql_string(path.name)}::VARCHAR AS source_file",
        "row_number() OVER ()::UBIGINT AS source_row_number",
        (
            f"md5({sql_string(table + ':')} || "
            "CAST(row_number() OVER () AS VARCHAR)) AS source_record_id"
        ),
        f"{sql_string(event_kind)}::VARCHAR AS event_kind",
        'CAST("event_date_time" AS TIMESTAMP) AS event_time',
        "concat('observer:', CAST(\"meta_client_name\" AS VARCHAR)) AS observer_id",
        (
            "concat('peer:', CAST(\"remote_peer_id_unique_key\" AS VARCHAR)) "
            "AS remote_peer_id"
        ),
        _field(columns, "direction", "direction", "VARCHAR"),
        _timestamp_field(columns, "opened", "opened_at"),
        _field(columns, "transient", "transient", "BOOLEAN"),
        _field(columns, "connection_age_ms", "connection_age_ms", "BIGINT"),
        _field(columns, "latency_ms", "latency_ms", "BIGINT"),
        _field(columns, "remote_protocol", "remote_protocol", "VARCHAR"),
        _field(columns, "remote_transport_protocol", "remote_transport_protocol", "VARCHAR"),
        _field(columns, "remote_port", "remote_port", "INTEGER"),
        _field(columns, "remote_ip", "remote_ip", "VARCHAR"),
        _field(columns, "remote_maddrs", "remote_maddrs", "VARCHAR"),
        _field(columns, "remote_multiaddr", "remote_multiaddr", "VARCHAR"),
        _field(columns, "protocols", "protocols_json", "VARCHAR", "to_json({column})"),
        _field(columns, "remote_agent_implementation", "remote_client", "VARCHAR"),
        _field(columns, "remote_agent_version", "remote_client_version", "VARCHAR"),
        _field(columns, "remote_agent_platform", "remote_platform", "VARCHAR"),
        _field(columns, "remote_geo_city", "remote_city", "VARCHAR"),
        _field(columns, "remote_geo_country", "remote_country", "VARCHAR"),
        _field(columns, "remote_geo_country_code", "remote_country_code", "VARCHAR"),
        _field(columns, "remote_geo_continent_code", "remote_continent_code", "VARCHAR"),
        _field(columns, "remote_geo_autonomous_system_number", "remote_asn", "UBIGINT"),
        _field(columns, "remote_geo_autonomous_system_organization", "remote_as_org", "VARCHAR"),
        _field(columns, "remote_geo_longitude", "remote_longitude", "DOUBLE"),
        _field(columns, "remote_geo_latitude", "remote_latitude", "DOUBLE"),
        _field(columns, "meta_client_implementation", "observer_client", "VARCHAR"),
        _field(columns, "meta_client_version", "observer_client_version", "VARCHAR"),
        _field(columns, "meta_client_os", "observer_os", "VARCHAR"),
        _field(columns, "meta_client_ip", "observer_ip", "VARCHAR"),
        _field(columns, "meta_client_geo_city", "observer_city", "VARCHAR"),
        _field(columns, "meta_client_geo_country", "observer_country", "VARCHAR"),
        _field(columns, "meta_client_geo_country_code", "observer_country_code", "VARCHAR"),
        _field(columns, "meta_client_geo_continent_code", "observer_continent_code", "VARCHAR"),
        _field(columns, "meta_client_geo_autonomous_system_number", "observer_asn", "UBIGINT"),
        _field(columns, "meta_client_geo_autonomous_system_organization", "observer_as_org", "VARCHAR"),
        _field(columns, "meta_client_geo_longitude", "observer_longitude", "DOUBLE"),
        _field(columns, "meta_client_geo_latitude", "observer_latitude", "DOUBLE"),
        _field(columns, "meta_network_name", "network_name", "VARCHAR"),
        _field(columns, "success", "identify_success", "BOOLEAN"),
        _field(columns, "error", "identify_error", "VARCHAR"),
        _field(columns, "transport", "identify_transport", "VARCHAR"),
        _field(columns, "security", "identify_security", "VARCHAR"),
        _field(columns, "muxer", "identify_muxer", "VARCHAR"),
        _field(columns, "observed_addr", "identify_observed_addr", "VARCHAR"),
        _field(columns, "listen_addrs", "identify_listen_addrs_json", "VARCHAR", "to_json({column})"),
    ]
    return (
        "SELECT\n  "
        + ",\n  ".join(fields)
        + f"\nFROM read_parquet({sql_string(path)})\n"
        + 'WHERE "event_date_time" IS NOT NULL\n'
        + '  AND "meta_client_name" IS NOT NULL\n'
        + '  AND "remote_peer_id_unique_key" IS NOT NULL'
    )


def _copy_atomic(connection: Any, query: str, destination: Path) -> None:
    stale_pattern = f".{destination.name}.tmp-*"
    for stale in destination.parent.glob(stale_pattern):
        if stale.is_file() and stale.stat().st_size == 0:
            stale.unlink(missing_ok=True)
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    connection.execute(
        f"COPY ({query}) TO {sql_string(temporary)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporary.replace(destination)


def normalize_dataset(layout: DatasetLayout) -> Path:
    duckdb = require_duckdb()
    manifest = read_json(layout.manifest_path)
    required_tables = {
        "libp2p_synthetic_heartbeat",
        "libp2p_connected",
        "libp2p_disconnected",
    }
    missing = required_tables - set(manifest["raw_files"])
    if missing:
        raise RuntimeError(f"dataset is missing required raw tables: {sorted(missing)}")
    tables = tuple(
        table for table in TABLE_EVENT_KIND if table in manifest["raw_files"]
    )
    connection = duckdb.connect()
    outputs: list[Path] = []
    row_counts: dict[str, int] = {}
    try:
        (layout.normalized / ".duckdb-tmp").mkdir(parents=True, exist_ok=True)
        connection.execute("SET TimeZone = 'UTC'")
        connection.execute(
            f"SET temp_directory = {sql_string(layout.normalized / '.duckdb-tmp')}"
        )
        for table in tables:
            source = raw_file(layout, table)
            destination = layout.normalized / f"{table}.parquet"
            query = _normalized_select(table, source, _columns(connection, source))
            _copy_atomic(connection, query, destination)
            outputs.append(destination)
            row_counts[table] = connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(destination)]
            ).fetchone()[0]

        combined = layout.normalized / "normalized_observations.parquet"
        sources = ", ".join(sql_string(path) for path in outputs)
        _copy_atomic(
            connection,
            f"SELECT * FROM read_parquet([{sources}], union_by_name=true)",
            combined,
        )
        row_counts["normalized_observations"] = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(combined)]
        ).fetchone()[0]
    finally:
        connection.close()

    result = {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "normalized_at": utc_now(),
        "inputs": {
            table: manifest["raw_files"][table]["sha256"] for table in tables
        },
        "row_counts": row_counts,
        "outputs": [str(path.relative_to(layout.path)) for path in outputs]
        + [str(combined.relative_to(layout.path))],
    }
    output_manifest = layout.normalized / "normalization_manifest.json"
    if output_manifest.exists():
        existing = read_json(output_manifest)
        if existing.get("inputs") != result["inputs"]:
            raise RuntimeError("normalized outputs belong to different raw inputs")
    else:
        atomic_write_json(output_manifest, result)
    return combined
