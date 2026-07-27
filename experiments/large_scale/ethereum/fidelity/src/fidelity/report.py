from __future__ import annotations

import math
import os
from collections import Counter
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .normalize import require_duckdb, sql_string
from .storage import DatasetLayout, atomic_write_json, read_json, utc_now


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.size: dict[str, int] = {}

    def add(self, node: str) -> None:
        if node not in self.parent:
            self.parent[node] = node
            self.size[node] = 1

    def find(self, node: str) -> str:
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while node != root:
            parent = self.parent[node]
            self.parent[node] = root
            node = parent
        return root

    def union(self, left: str, right: str) -> None:
        self.add(left)
        self.add(right)
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.size[left_root] < self.size[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        self.size[left_root] += self.size[right_root]

    def component_sizes(self) -> list[int]:
        counts = Counter(self.find(node) for node in self.parent)
        return sorted(counts.values(), reverse=True)


def _percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _graph_stats(connection: Any, edges: Path) -> tuple[dict[str, Any], Counter[str]]:
    union_find = UnionFind()
    degree: Counter[str] = Counter()
    observer_degree: Counter[str] = Counter()
    remote_observer_count: Counter[str] = Counter()
    cursor = connection.execute(
        "SELECT source, target FROM read_parquet(?)", [str(edges)]
    )
    edge_count = 0
    while rows := cursor.fetchmany(100_000):
        for source, target in rows:
            source = str(source)
            target = str(target)
            union_find.union(source, target)
            degree[source] += 1
            degree[target] += 1
            observer_degree[source] += 1
            remote_observer_count[target] += 1
            edge_count += 1
    components = union_find.component_sizes()
    observer_values = list(observer_degree.values())
    return (
        {
            "nodes": len(union_find.parent),
            "edges": edge_count,
            "observers": len(observer_degree),
            "remote_peers": len(remote_observer_count),
            "components": len(components),
            "largest_component_nodes": components[0] if components else 0,
            "largest_component_fraction": (
                components[0] / len(union_find.parent) if components else 0.0
            ),
            "mean_undirected_degree": (
                2 * edge_count / len(union_find.parent) if union_find.parent else 0.0
            ),
            "observer_degree_p50": _percentile(observer_values, 0.50),
            "observer_degree_p95": _percentile(observer_values, 0.95),
            "observer_degree_max": max(observer_values, default=0),
            "remote_peers_seen_by_multiple_observers": sum(
                value > 1 for value in remote_observer_count.values()
            ),
            "component_sizes_top20": components[:20],
        },
        observer_degree,
    )


def _write_graphml(connection: Any, nodes: Path, edges: Path, destination: Path) -> None:
    if destination.exists():
        return
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    with temporary.open("w") as output:
        output.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        output.write('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">\n')
        output.write('  <key id="role" for="node" attr.name="role" attr.type="string"/>\n')
        output.write('  <graph id="G" edgedefault="undirected">\n')
        cursor = connection.execute(
            "SELECT node_id, node_role FROM read_parquet(?) ORDER BY node_id",
            [str(nodes)],
        )
        while rows := cursor.fetchmany(100_000):
            for node_id, role in rows:
                output.write(
                    f'    <node id="{escape(str(node_id))}"><data key="role">'
                    f"{escape(str(role))}</data></node>\n"
                )
        cursor = connection.execute(
            "SELECT source, target FROM read_parquet(?) ORDER BY source, target",
            [str(edges)],
        )
        edge_id = 0
        while rows := cursor.fetchmany(100_000):
            for source, target in rows:
                output.write(
                    f'    <edge id="e{edge_id}" source="{escape(str(source))}" '
                    f'target="{escape(str(target))}"/>\n'
                )
                edge_id += 1
        output.write("  </graph>\n</graphml>\n")
    temporary.replace(destination)


def _distribution(connection: Any, source: Path, column: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = connection.execute(
        f"""
        SELECT coalesce(CAST({column} AS VARCHAR), '<unknown>') AS value, count(*) AS count
        FROM read_parquet(?)
        GROUP BY value
        ORDER BY count DESC, value
        LIMIT {limit}
        """,
        [str(source)],
    ).fetchall()
    return [{"value": row[0], "count": int(row[1])} for row in rows]


def _write_timeseries(connection: Any, observations: Path, destination: Path) -> None:
    if destination.exists():
        return
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    connection.execute(
        f"""
        COPY (
            SELECT
                time_bucket(INTERVAL '1 minute', event_time) AS minute,
                count(DISTINCT observer_id)::UBIGINT AS observer_count,
                count(DISTINCT remote_peer_id)::UBIGINT AS remote_peer_count,
                count(DISTINCT (observer_id, remote_peer_id))::UBIGINT AS edge_count,
                count(*)::UBIGINT AS heartbeat_rows
            FROM read_parquet({sql_string(observations)})
            WHERE event_kind = 'heartbeat'
            GROUP BY minute
            ORDER BY minute
        ) TO {sql_string(temporary)} (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )
    temporary.replace(destination)


def _plot(
    report_dir: Path,
    g0_degree: Counter[str],
    c_degree: Counter[str],
    durations: list[int],
) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    figure, axis = plt.subplots(figsize=(6.4, 4.0))
    for label, values in (("G0", list(g0_degree.values())), ("C", list(c_degree.values()))):
        ordered = sorted(values)
        if ordered:
            y = [(index + 1) / len(ordered) for index in range(len(ordered))]
            axis.step(ordered, y, where="post", label=label)
    axis.set_xlabel("Visible peers per Xatu observer")
    axis.set_ylabel("Empirical CDF")
    axis.grid(alpha=0.25)
    axis.legend()
    figure.tight_layout()
    figure.savefig(report_dir / "observer_degree_cdf.pdf")
    figure.savefig(report_dir / "observer_degree_cdf.png", dpi=180)
    plt.close(figure)

    if durations:
        ordered = sorted(value / 1000.0 for value in durations if value >= 0)
        figure, axis = plt.subplots(figsize=(6.4, 4.0))
        y = [(index + 1) / len(ordered) for index in range(len(ordered))]
        axis.step(ordered, y, where="post", color="#b44d32")
        axis.set_xscale("log")
        axis.set_xlabel("Completed connection duration (seconds, log scale)")
        axis.set_ylabel("Empirical CDF")
        axis.grid(alpha=0.25)
        figure.tight_layout()
        figure.savefig(report_dir / "connection_duration_cdf.pdf")
        figure.savefig(report_dir / "connection_duration_cdf.png", dpi=180)
        plt.close(figure)
    return True


def generate_report(layout: DatasetLayout, run_dir: Path) -> Path:
    report_dir = layout.reports / run_dir.name
    report_dir.mkdir(parents=True, exist_ok=True)
    observations = layout.normalized / "normalized_observations.parquet"
    g0_edges = run_dir / "g0_edges.parquet"
    c_edges = run_dir / "candidate_edges.parquet"
    intervals = run_dir / "connection_intervals.parquet"

    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone = 'UTC'")
        g0_stats, g0_degree = _graph_stats(connection, g0_edges)
        c_stats, c_degree = _graph_stats(connection, c_edges)
        interval_stats = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE left_censored),
                count(*) FILTER (WHERE right_censored),
                count(*) FILTER (WHERE NOT left_censored AND NOT right_censored),
                quantile_cont(duration_ms, 0.50) FILTER (
                    WHERE NOT left_censored AND NOT right_censored AND duration_ms >= 0
                ),
                quantile_cont(duration_ms, 0.95) FILTER (
                    WHERE NOT left_censored AND NOT right_censored AND duration_ms >= 0
                )
            FROM read_parquet(?)
            """,
            [str(intervals)],
        ).fetchone()
        durations = [
            int(row[0])
            for row in connection.execute(
                """
                SELECT duration_ms
                FROM (
                    SELECT duration_ms FROM read_parquet(?)
                    WHERE NOT left_censored
                      AND NOT right_censored
                      AND duration_ms IS NOT NULL
                      AND duration_ms >= 0
                ) AS completed
                USING SAMPLE reservoir(100000 ROWS) REPEATABLE (1337)
                """,
                [str(intervals)],
            ).fetchall()
        ]
        missing_g0 = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?) AS initial
            ANTI JOIN read_parquet(?) AS candidate
              ON initial.source = candidate.source AND initial.target = candidate.target
            """,
            [str(g0_edges), str(c_edges)],
        ).fetchone()[0]
        candidate_nodes = run_dir / "candidate_nodes.parquet"
        report = {
            "schema_version": 1,
            "dataset_id": layout.dataset_id,
            "run_id": run_dir.name,
            "generated_at": utc_now(),
            "interpretation": (
                "This is the union of Xatu ego-network observations, not a complete "
                "ground-truth Ethereum mainnet adjacency graph."
            ),
            "g0": g0_stats,
            "c": c_stats,
            "subset_check": {
                "g0_edges_in_c": g0_stats["edges"],
                "replacement_edges": c_stats["edges"] - g0_stats["edges"],
                "g0_edges_missing_from_c": int(missing_g0),
                "valid": missing_g0 == 0,
            },
            "connection_intervals": {
                "total": int(interval_stats[0]),
                "left_censored": int(interval_stats[1]),
                "right_censored": int(interval_stats[2]),
                "complete": int(interval_stats[3]),
                "duration_ms_p50_complete": interval_stats[4],
                "duration_ms_p95_complete": interval_stats[5],
                "duration_plot_sample_size": len(durations),
            },
            "attributes": {
                "client_top30": _distribution(connection, candidate_nodes, "client"),
                "asn_top30": _distribution(connection, candidate_nodes, "asn"),
                "country_top30": _distribution(connection, candidate_nodes, "country_code"),
            },
        }
        if not report["subset_check"]["valid"]:
            raise RuntimeError("report detected that G0 is not a subset of C")

        _write_timeseries(connection, observations, report_dir / "topology_timeseries.parquet")
        _write_graphml(
            connection,
            run_dir / "g0_nodes.parquet",
            g0_edges,
            report_dir / "g0.graphml",
        )
        _write_graphml(
            connection,
            candidate_nodes,
            c_edges,
            report_dir / "candidate_graph.graphml",
        )
        report["plots_generated"] = _plot(
            report_dir, g0_degree, c_degree, durations
        )
    finally:
        connection.close()

    output = report_dir / "coverage_report.json"
    atomic_write_json(output, report)
    return output
