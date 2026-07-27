from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from .normalize import require_duckdb, sql_string
from .storage import DatasetLayout, atomic_write_json, read_json, utc_now


MODEL_ALGORITHM = "xatu-remote-propensity-v4"
PAIR_FEATURES = ("same_asn", "same_org", "same_country", "same_city")


@dataclass
class NodeProfile:
    node_id: str
    client: str
    asn: str
    as_org: str
    country: str
    city: str
    availability: float
    visibility: float
    formations: int
    retention_exposures: int
    retained: int
    sessions: int
    session_exposure_ms: float
    observed_ends: int
    duration_p50_ms: float
    q_raw: float = 0.0
    r_raw: float = 0.0
    q: float = 0.0
    r: float = 0.0
    formation_opportunities: float = 0.0


def _clip_probability(value: float) -> float:
    return min(1.0 - 1e-9, max(1e-9, float(value)))


def _logit(value: float) -> float:
    probability = _clip_probability(value)
    return math.log(probability / (1.0 - probability))


def _sigmoid_array(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {name: None for name in ("p05", "p25", "p50", "p75", "p95")}
    result = np.quantile(np.asarray(values, dtype=float), [0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        name: float(value)
        for name, value in zip(("p05", "p25", "p50", "p75", "p95"), result)
    }


def _components(node_count: int, edges: Iterable[tuple[int, int]]) -> list[list[int]]:
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
    groups: dict[int, list[int]] = {}
    for node in range(node_count):
        groups.setdefault(find(node), []).append(node)
    return sorted(groups.values(), key=len, reverse=True)


def _degrees(node_count: int, edges: Iterable[tuple[int, int]]) -> list[int]:
    values = [0] * node_count
    for left, right in edges:
        values[left] += 1
        values[right] += 1
    return values


def _is_connected(node_count: int, edges: set[tuple[int, int]]) -> bool:
    return len(_components(node_count, edges)) == 1


def _pair_features(left: NodeProfile, right: NodeProfile) -> np.ndarray:
    return np.asarray(
        [
            float(bool(left.asn) and left.asn == right.asn),
            float(bool(left.as_org) and left.as_org == right.as_org),
            float(bool(left.country) and left.country == right.country),
            float(bool(left.city) and left.city == right.city),
        ],
        dtype=float,
    )


def _binary_log_loss(y: np.ndarray, probability: np.ndarray) -> float:
    clipped = np.clip(probability, 1e-9, 1.0 - 1e-9)
    return float(-np.mean(y * np.log(clipped) + (1.0 - y) * np.log(1.0 - clipped)))


def _fit_logistic(
    features: np.ndarray,
    outcomes: np.ndarray,
    offsets: np.ndarray,
    *,
    include_intercept: bool,
    regularization: float = 2.0,
    max_iterations: int = 50,
) -> np.ndarray:
    if include_intercept:
        design = np.column_stack((np.ones(len(features)), features))
    else:
        design = features
    if design.shape[1] == 0:
        return np.zeros(0, dtype=float)
    coefficients = np.zeros(design.shape[1], dtype=float)
    penalty = np.eye(design.shape[1], dtype=float) * regularization
    if include_intercept:
        penalty[0, 0] = 0.0
    for _ in range(max_iterations):
        linear = offsets + design @ coefficients
        probability = _sigmoid_array(linear)
        weights = np.maximum(probability * (1.0 - probability), 1e-6)
        gradient = design.T @ (probability - outcomes) + penalty @ coefficients
        hessian = (design.T * weights) @ design + penalty
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        coefficients -= step
        if float(np.max(np.abs(step))) < 1e-7:
            break
    return coefficients


def _predict_logistic(
    features: np.ndarray,
    offsets: np.ndarray,
    coefficients: np.ndarray,
    include_intercept: bool,
) -> np.ndarray:
    if include_intercept:
        design = np.column_stack((np.ones(len(features)), features))
    else:
        design = features
    return _sigmoid_array(offsets + design @ coefficients)


def _time_split(times: Sequence[Any], seed: int) -> tuple[np.ndarray, np.ndarray]:
    count = len(times)
    if count < 10:
        indices = list(range(count))
        random.Random(seed).shuffle(indices)
        boundary = max(1, int(count * 0.8))
        return np.asarray(indices[:boundary]), np.asarray(indices[boundary:] or indices[-1:])
    order = np.argsort(np.asarray([str(value) for value in times]))
    boundary = min(count - 1, max(1, int(count * 0.8)))
    return order[:boundary], order[boundary:]


def _select_pair_features(
    features: np.ndarray,
    outcomes: np.ndarray,
    offsets: np.ndarray,
    times: Sequence[Any],
    *,
    include_intercept: bool,
    seed: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    feature_count = features.shape[1]
    if len(features) < 40 or len(np.unique(outcomes)) < 2 and include_intercept:
        coefficients = np.zeros(feature_count + int(include_intercept), dtype=float)
        if include_intercept and len(outcomes):
            coefficients[0] = _logit(float(np.mean(outcomes))) - float(np.mean(offsets))
        return coefficients, {
            "status": "insufficient-samples",
            "samples": int(len(features)),
            "retained_features": [],
            "dropped_features": list(PAIR_FEATURES),
        }

    train, test = _time_split(times, seed)
    baseline_features = np.empty((len(features), 0), dtype=float)
    baseline_coefficients = _fit_logistic(
        baseline_features[train], outcomes[train], offsets[train],
        include_intercept=include_intercept,
    )
    baseline_probability = _predict_logistic(
        baseline_features[test], offsets[test], baseline_coefficients, include_intercept
    )
    baseline_loss = _binary_log_loss(outcomes[test], baseline_probability)

    half = max(1, len(train) // 2)
    stable_sign: dict[int, bool] = {}
    for index in range(feature_count):
        first = _fit_logistic(
            features[train[:half], index:index + 1],
            outcomes[train[:half]],
            offsets[train[:half]],
            include_intercept=include_intercept,
        )[-1]
        second = _fit_logistic(
            features[train[half:], index:index + 1],
            outcomes[train[half:]],
            offsets[train[half:]],
            include_intercept=include_intercept,
        )[-1]
        stable_sign[index] = bool(
            abs(first) >= 0.02 and abs(second) >= 0.02 and first * second > 0
        )

    retained: list[int] = []
    current_loss = baseline_loss
    while True:
        best: tuple[float, int, np.ndarray] | None = None
        for index in range(feature_count):
            if index in retained or not stable_sign[index]:
                continue
            candidate = retained + [index]
            fitted = _fit_logistic(
                features[train][:, candidate], outcomes[train], offsets[train],
                include_intercept=include_intercept,
            )
            probability = _predict_logistic(
                features[test][:, candidate], offsets[test], fitted, include_intercept
            )
            loss = _binary_log_loss(outcomes[test], probability)
            if best is None or loss < best[0]:
                best = (loss, index, fitted)
        if best is None or current_loss - best[0] < 5e-4:
            break
        current_loss = best[0]
        retained.append(best[1])

    selected = features[:, retained] if retained else baseline_features
    fitted = _fit_logistic(
        selected[train], outcomes[train], offsets[train],
        include_intercept=include_intercept,
    )
    probability = _predict_logistic(selected[test], offsets[test], fitted, include_intercept)
    output = np.zeros(feature_count + int(include_intercept), dtype=float)
    position = 0
    if include_intercept:
        output[0] = fitted[0]
        position = 1
    for fitted_index, feature_index in enumerate(retained):
        output[position + feature_index] = fitted[fitted_index + int(include_intercept)]

    return output, {
        "status": "fit",
        "samples": int(len(features)),
        "train_samples": int(len(train)),
        "held_out_samples": int(len(test)),
        "baseline_log_loss": baseline_loss,
        "selected_log_loss": _binary_log_loss(outcomes[test], probability),
        "held_out_accuracy": float(np.mean((probability >= 0.5) == outcomes[test])),
        "retained_features": [PAIR_FEATURES[index] for index in retained],
        "dropped_features": [
            name for index, name in enumerate(PAIR_FEATURES) if index not in retained
        ],
        "stable_sign": {
            PAIR_FEATURES[index]: stable_sign[index] for index in range(feature_count)
        },
    }


def _top_categories(profiles: Sequence[NodeProfile], attribute: str, limit: int) -> list[str]:
    counts = Counter(getattr(profile, attribute) for profile in profiles)
    counts.pop("", None)
    return [value for value, _ in counts.most_common(limit)]


def _ridge_node_effects(
    profiles: list[NodeProfile],
) -> dict[str, Any]:
    clients = _top_categories(profiles, "client", 8)
    countries = _top_categories(profiles, "country", 10)
    asns = _top_categories(profiles, "asn", 16)
    organizations = _top_categories(profiles, "as_org", 12)

    total_session_exposure_ms = sum(
        profile.session_exposure_ms for profile in profiles
    )
    total_observed_ends = sum(profile.observed_ends for profile in profiles)
    global_censor_aware_lifetime_ms = (
        total_session_exposure_ms / total_observed_ends
        if total_observed_ends
        else 600_000.0
    )

    numeric = np.asarray(
        [
            [
                _logit(profile.availability),
                _logit(profile.visibility),
                math.log1p(profile.sessions),
                math.log1p(profile.duration_p50_ms / 60_000.0),
                math.log1p(
                    (
                        profile.session_exposure_ms
                        + 10.0 * global_censor_aware_lifetime_ms
                    )
                    / (profile.observed_ends + 10.0)
                    / 60_000.0
                ),
            ]
            for profile in profiles
        ],
        dtype=float,
    )
    means = numeric.mean(axis=0)
    standard_deviations = numeric.std(axis=0)
    standard_deviations[standard_deviations < 1e-9] = 1.0
    numeric = (numeric - means) / standard_deviations
    categorical_columns: list[np.ndarray] = []
    names = [
        "availability",
        "visibility",
        "log_sessions",
        "log_duration_p50",
        "log_censor_aware_lifetime",
    ]
    for attribute, values in (
        ("client", clients),
        ("country", countries),
        ("asn", asns),
        ("as_org", organizations),
    ):
        for value in values:
            categorical_columns.append(
                np.asarray([float(getattr(profile, attribute) == value) for profile in profiles])
            )
            names.append(f"{attribute}={value}")
    design = np.column_stack(
        [np.ones(len(profiles)), numeric] + categorical_columns
    )
    names = ["intercept"] + names
    penalty = np.eye(design.shape[1]) * 10.0
    penalty[0, 0] = 0.0

    def fit(target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ target)
        return coefficients, design @ coefficients

    q_target = np.asarray([profile.q_raw for profile in profiles])
    r_target = np.asarray([profile.r_raw for profile in profiles])
    q_coefficients, q_prediction = fit(q_target)
    r_coefficients, r_prediction = fit(r_target)
    for index, profile in enumerate(profiles):
        q_reliability = profile.formations / (profile.formations + 12.0)
        r_reliability = profile.retention_exposures / (
            profile.retention_exposures + 120.0
        )
        profile.q = q_reliability * profile.q_raw + (1.0 - q_reliability) * q_prediction[index]
        profile.r = r_reliability * profile.r_raw + (1.0 - r_reliability) * r_prediction[index]

    for attribute in ("q", "r"):
        values = np.asarray([getattr(profile, attribute) for profile in profiles])
        center = float(np.median(values))
        scale = float(np.std(values)) or 1.0
        for profile in profiles:
            setattr(profile, attribute, float(np.clip((getattr(profile, attribute) - center) / scale, -3.0, 3.0)))

    return {
        "feature_names": names,
        "numeric_means": means.tolist(),
        "numeric_standard_deviations": standard_deviations.tolist(),
        "q_coefficients": q_coefficients.tolist(),
        "r_coefficients": r_coefficients.tolist(),
        "regularization": 10.0,
        "global_censor_aware_lifetime_ms": global_censor_aware_lifetime_ms,
        "censoring_semantics": (
            "left-censored sessions excluded; right-censored exposure retained "
            "without counting an observed end"
        ),
        "partial_pooling": {
            "formation_prior_events": 12.0,
            "retention_prior_exposures": 120.0,
        },
    }


def _load_node_profiles(
    connection: Any,
    run_dir: Path,
    step_minutes: int,
    *,
    fit_effects: bool = True,
    observer_ids: Sequence[str] | None = None,
) -> tuple[list[NodeProfile], dict[str, Any]]:
    snapshot_dir = run_dir / "snapshots"
    edges = snapshot_dir / "snapshot_edges.parquet"
    observer_profiles = snapshot_dir / "observer_profiles.parquet"
    sessions = snapshot_dir / "heartbeat_sessions.parquet"
    candidate_nodes = run_dir / "candidate_nodes.parquet"
    step = f"INTERVAL '{step_minutes} minutes'"
    observer_predicate = (
        "observer.behavior_cohort = 'bounded'"
        if observer_ids is None
        else "edge.source IN ("
        + ",".join(sql_string(value) for value in observer_ids)
        + ")"
    )
    session_observer_predicate = (
        "observer.behavior_cohort = 'bounded'"
        if observer_ids is None
        else "session.source IN ("
        + ",".join(sql_string(value) for value in observer_ids)
        + ")"
    )

    bounds = connection.execute(
        "SELECT min(bucket_start), max(bucket_start), count(DISTINCT bucket_start) "
        "FROM read_parquet(?)",
        [str(edges)],
    ).fetchone()
    if observer_ids is None:
        bounded_observers = int(
            connection.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE behavior_cohort='bounded'",
                [str(observer_profiles)],
            ).fetchone()[0]
        )
    else:
        bounded_observers = int(
            connection.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE source IN ("
                + ",".join("?" for _ in observer_ids)
                + ")",
                [str(observer_profiles), *observer_ids],
            ).fetchone()[0]
        )
    if bounded_observers < 2:
        raise RuntimeError("at least two bounded observers are required")

    transition_query = f"""
        WITH bounded AS MATERIALIZED (
            SELECT edge.bucket_start, edge.source, edge.target
            FROM read_parquet({sql_string(edges)}) AS edge
            JOIN read_parquet({sql_string(observer_profiles)}) AS observer USING (source)
            WHERE {observer_predicate}
        ), states AS (
            SELECT
                current.bucket_start,
                current.source,
                current.target,
                retained.target IS NOT NULL AS retained,
                previous.target IS NULL AS newly_formed
            FROM bounded AS current
            LEFT JOIN bounded AS retained
              ON retained.source = current.source
             AND retained.target = current.target
             AND retained.bucket_start = current.bucket_start + {step}
            LEFT JOIN bounded AS previous
              ON previous.source = current.source
             AND previous.target = current.target
             AND previous.bucket_start = current.bucket_start - {step}
        )
        SELECT
            target,
            count(DISTINCT bucket_start)::BIGINT AS observed_buckets,
            count(DISTINCT source)::BIGINT AS visible_observers,
            count(*)::BIGINT AS observer_bucket_edges,
            count(*) FILTER (WHERE bucket_start <= CAST({sql_string(bounds[1])} AS TIMESTAMP) - {step})::BIGINT
                AS retention_exposures,
            count(*) FILTER (
                WHERE bucket_start <= CAST({sql_string(bounds[1])} AS TIMESTAMP) - {step}
                  AND retained
            )::BIGINT AS retained,
            count(*) FILTER (
                WHERE bucket_start >= CAST({sql_string(bounds[0])} AS TIMESTAMP) + {step}
                  AND newly_formed
            )::BIGINT AS formations
        FROM states
        GROUP BY target
    """
    transition_rows = {
        str(row[0]): row[1:] for row in connection.execute(transition_query).fetchall()
    }
    session_rows = {
        str(row[0]): row[1:]
        for row in connection.execute(
            f"""
            SELECT
                session.target,
                count(*)::BIGINT AS sessions,
                sum(greatest(session.observed_duration_ms, 60000))::DOUBLE AS exposure_ms,
                count(*) FILTER (WHERE NOT session.right_censored)::BIGINT AS observed_ends,
                quantile_cont(session.observed_duration_ms, 0.50)::DOUBLE AS duration_p50_ms
            FROM read_parquet(?) AS session
            JOIN read_parquet(?) AS observer USING (source)
            WHERE {session_observer_predicate}
              AND session.likely_stable
              AND NOT session.left_censored
            GROUP BY session.target
            """,
            [str(sessions), str(observer_profiles)],
        ).fetchall()
    }
    node_rows = connection.execute(
        """
        SELECT node_id, client, asn, as_org, country_code, city
        FROM read_parquet(?)
        WHERE node_role = 'remote'
        ORDER BY node_id
        """,
        [str(candidate_nodes)],
    ).fetchall()
    total_buckets = int(bounds[2])
    profiles: list[NodeProfile] = []
    for row in node_rows:
        node_id = str(row[0])
        transition = transition_rows.get(node_id, (0, 0, 0, 0, 0, 0))
        session = session_rows.get(node_id, (0, 0.0, 0, 0.0))
        profiles.append(
            NodeProfile(
                node_id=node_id,
                client=str(row[1] or ""),
                asn=str(row[2] or ""),
                as_org=str(row[3] or ""),
                country=str(row[4] or ""),
                city=str(row[5] or ""),
                availability=float(transition[0]) / max(1, total_buckets),
                visibility=float(transition[1]) / bounded_observers,
                formations=int(transition[5]),
                retention_exposures=int(transition[3]),
                retained=int(transition[4]),
                sessions=int(session[0]),
                session_exposure_ms=float(session[1] or 0.0),
                observed_ends=int(session[2]),
                duration_p50_ms=float(session[3] or 0.0),
                formation_opportunities=max(
                    1.0,
                    float(transition[0]) / max(1, total_buckets)
                    * total_buckets
                    * bounded_observers,
                ),
            )
        )

    retention_exposures = sum(profile.retention_exposures for profile in profiles)
    retained = sum(profile.retained for profile in profiles)
    opportunities = sum(
        max(1.0, profile.availability * total_buckets * bounded_observers)
        for profile in profiles
    )
    formations = sum(profile.formations for profile in profiles)
    global_retention = retained / retention_exposures if retention_exposures else 0.8
    global_formation_rate = formations / opportunities if opportunities else 0.01
    global_formation_rate = max(global_formation_rate, 1e-8)

    ridge = None
    if fit_effects:
        _fit_profile_effects(profiles, global_formation_rate, global_retention)
        ridge = _ridge_node_effects(profiles)
    return profiles, {
        "first_bucket": bounds[0].isoformat(),
        "last_bucket": bounds[1].isoformat(),
        "bucket_count": total_buckets,
        "bounded_observer_count": bounded_observers,
        "global_retention": global_retention,
        "global_formation_rate_per_opportunity": global_formation_rate,
        "retention_exposures": retention_exposures,
        "retained_edges": retained,
        "formation_events": formations,
        "ridge": ridge,
    }


def _fit_profile_effects(
    profiles: list[NodeProfile],
    global_formation_rate: float,
    global_retention: float,
) -> None:
    """Apply the empirical-Bayes node estimators to pre-aggregated counts."""
    global_formation_rate = max(global_formation_rate, 1e-8)
    for profile in profiles:
        opportunity = max(
            1.0,
            profile.formation_opportunities
            or profile.availability,
        )
        formation_rate = (
            profile.formations + 20.0 * global_formation_rate
        ) / (opportunity + 20.0)
        retention = (
            profile.retained + 50.0 * global_retention
        ) / (profile.retention_exposures + 50.0)
        profile.q_raw = float(
            np.clip(
                math.log(max(formation_rate, 1e-12) / global_formation_rate),
                -6.0,
                6.0,
            )
        )
        profile.r_raw = float(
            np.clip(_logit(retention) - _logit(global_retention), -6.0, 6.0)
        )


def _observer_profiles(connection: Any, run_dir: Path) -> dict[str, NodeProfile]:
    rows = connection.execute(
        """
        SELECT node_id, client, asn, as_org, country_code, city
        FROM read_parquet(?) WHERE node_role='observer'
        """,
        [str(run_dir / "candidate_nodes.parquet")],
    ).fetchall()
    return {
        str(row[0]): NodeProfile(
            node_id=str(row[0]),
            client=str(row[1] or ""),
            asn=str(row[2] or ""),
            as_org=str(row[3] or ""),
            country=str(row[4] or ""),
            city=str(row[5] or ""),
            availability=1.0,
            visibility=1.0,
            formations=0,
            retention_exposures=0,
            retained=0,
            sessions=0,
            session_exposure_ms=0.0,
            observed_ends=0,
            duration_p50_ms=0.0,
        )
        for row in rows
    }


def _formation_samples(
    connection: Any,
    run_dir: Path,
    step_minutes: int,
    sample_limit: int,
    seed: int,
    observer_ids: Sequence[str] | None = None,
) -> list[tuple[Any, ...]]:
    snapshot_dir = run_dir / "snapshots"
    edges = snapshot_dir / "snapshot_edges.parquet"
    observers = snapshot_dir / "observer_profiles.parquet"
    step = f"INTERVAL '{step_minutes} minutes'"
    observer_predicate = (
        "observer.behavior_cohort='bounded'"
        if observer_ids is None
        else "edge.source IN ("
        + ",".join(sql_string(value) for value in observer_ids)
        + ")"
    )
    return connection.execute(
        f"""
        WITH bounded AS MATERIALIZED (
            SELECT edge.bucket_start, edge.source, edge.target
            FROM read_parquet({sql_string(edges)}) AS edge
            JOIN read_parquet({sql_string(observers)}) AS observer USING (source)
            WHERE {observer_predicate}
        ), bounds AS (
            SELECT min(bucket_start) AS first_bucket FROM bounded
        ), formed AS (
            SELECT current.bucket_start, current.source, current.target
            FROM bounded AS current
            LEFT JOIN bounded AS previous
              ON previous.source=current.source
             AND previous.target=current.target
             AND previous.bucket_start=current.bucket_start-{step}
            CROSS JOIN bounds
            WHERE previous.target IS NULL
              AND current.bucket_start >= bounds.first_bucket+{step}
            ORDER BY hash(current.source, current.target, current.bucket_start, {seed})
            LIMIT {sample_limit * 2}
        ), available AS (
            SELECT DISTINCT bucket_start, target FROM bounded
        ), indexed AS (
            SELECT
                bucket_start,
                target,
                row_number() OVER (PARTITION BY bucket_start ORDER BY hash(target)) AS position,
                count(*) OVER (PARTITION BY bucket_start) AS candidates
            FROM available
        ), selected AS (
            SELECT
                formed.*,
                1 + CAST(
                    hash(formed.source, formed.target, formed.bucket_start, {seed + 17})
                    % available.candidates AS BIGINT
                ) AS negative_position
            FROM formed
            JOIN (
                SELECT bucket_start, max(candidates) AS candidates
                FROM indexed GROUP BY bucket_start
            ) AS available USING (bucket_start)
        )
        SELECT selected.bucket_start, selected.source, selected.target, negative.target
        FROM selected
        JOIN indexed AS negative
          ON negative.bucket_start=selected.bucket_start
         AND negative.position=selected.negative_position
        LEFT JOIN bounded AS already_connected
          ON already_connected.bucket_start=selected.bucket_start
         AND already_connected.source=selected.source
         AND already_connected.target=negative.target
        WHERE negative.target<>selected.target AND already_connected.target IS NULL
        ORDER BY hash(
            selected.source,
            selected.target,
            selected.bucket_start,
            negative.target,
            {seed + 31}
        )
        LIMIT {sample_limit}
        """
    ).fetchall()


def _retention_samples(
    connection: Any,
    run_dir: Path,
    step_minutes: int,
    sample_limit: int,
    seed: int,
    observer_ids: Sequence[str] | None = None,
) -> list[tuple[Any, ...]]:
    snapshot_dir = run_dir / "snapshots"
    edges = snapshot_dir / "snapshot_edges.parquet"
    observers = snapshot_dir / "observer_profiles.parquet"
    step = f"INTERVAL '{step_minutes} minutes'"
    observer_predicate = (
        "observer.behavior_cohort='bounded'"
        if observer_ids is None
        else "edge.source IN ("
        + ",".join(sql_string(value) for value in observer_ids)
        + ")"
    )
    return connection.execute(
        f"""
        WITH bounded AS MATERIALIZED (
            SELECT edge.bucket_start, edge.source, edge.target
            FROM read_parquet({sql_string(edges)}) AS edge
            JOIN read_parquet({sql_string(observers)}) AS observer USING (source)
            WHERE {observer_predicate}
        ), bounds AS (
            SELECT max(bucket_start) AS last_bucket FROM bounded
        )
        SELECT
            current.bucket_start,
            current.source,
            current.target,
            retained.target IS NOT NULL AS retained
        FROM bounded AS current
        LEFT JOIN bounded AS retained
          ON retained.source=current.source
         AND retained.target=current.target
         AND retained.bucket_start=current.bucket_start+{step}
        CROSS JOIN bounds
        WHERE current.bucket_start <= bounds.last_bucket-{step}
        ORDER BY hash(current.source, current.target, current.bucket_start, {seed})
        LIMIT {sample_limit}
        """
    ).fetchall()


def _fit_pair_models(
    connection: Any,
    run_dir: Path,
    profiles: list[NodeProfile],
    step_minutes: int,
    sample_limit: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    by_id = {profile.node_id: profile for profile in profiles}
    observers = _observer_profiles(connection, run_dir)

    formation_rows = _formation_samples(
        connection, run_dir, step_minutes, sample_limit, seed
    )
    formation_features: list[np.ndarray] = []
    formation_offsets: list[float] = []
    formation_times: list[Any] = []
    for bucket, source, positive_id, negative_id in formation_rows:
        observer = observers.get(str(source))
        positive = by_id.get(str(positive_id))
        negative = by_id.get(str(negative_id))
        if observer is None or positive is None or negative is None:
            continue
        formation_features.append(
            _pair_features(observer, positive) - _pair_features(observer, negative)
        )
        formation_offsets.append(positive.q - negative.q)
        formation_times.append(bucket)
    formation_x = np.asarray(formation_features, dtype=float).reshape((-1, len(PAIR_FEATURES)))
    formation_y = np.ones(len(formation_x), dtype=float)
    formation_offset = np.asarray(formation_offsets, dtype=float)
    formation_coefficients, formation_report = _select_pair_features(
        formation_x,
        formation_y,
        formation_offset,
        formation_times,
        include_intercept=False,
        seed=seed,
    )

    retention_rows = _retention_samples(
        connection, run_dir, step_minutes, sample_limit, seed + 1
    )
    retention_features: list[np.ndarray] = []
    retention_offsets: list[float] = []
    retention_outcomes: list[float] = []
    retention_times: list[Any] = []
    for bucket, source, target_id, retained in retention_rows:
        observer = observers.get(str(source))
        target = by_id.get(str(target_id))
        if observer is None or target is None:
            continue
        retention_features.append(_pair_features(observer, target))
        retention_offsets.append(target.r)
        retention_outcomes.append(float(retained))
        retention_times.append(bucket)
    retention_x = np.asarray(retention_features, dtype=float).reshape((-1, len(PAIR_FEATURES)))
    retention_y = np.asarray(retention_outcomes, dtype=float)
    retention_offset = np.asarray(retention_offsets, dtype=float)
    retention_coefficients, retention_report = _select_pair_features(
        retention_x,
        retention_y,
        retention_offset,
        retention_times,
        include_intercept=True,
        seed=seed + 1,
    )
    return formation_coefficients, retention_coefficients, {
        "formation": formation_report,
        "retention": retention_report,
    }


def _select_nodes(
    connection: Any,
    run_dir: Path,
    profiles: list[NodeProfile],
    node_count: int,
    seed: int,
    observer_ids: Sequence[str] | None = None,
) -> tuple[list[NodeProfile], str, list[NodeProfile]]:
    by_id = {profile.node_id: profile for profile in profiles}
    reconstruction = read_json(run_dir / "reconstruction_manifest.json")
    bucket = reconstruction["snapshot"]["bucket_start"]
    observer_predicate = (
        "observer.behavior_cohort='bounded'"
        if observer_ids is None
        else "edge.source IN ("
        + ",".join(sql_string(value) for value in observer_ids)
        + ")"
    )
    available_ids = [
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT DISTINCT edge.target
            FROM read_parquet(?) AS edge
            JOIN read_parquet(?) AS observer USING (source)
            WHERE edge.bucket_start=CAST(? AS TIMESTAMP)
              AND {observer_predicate}
            ORDER BY edge.target
            """,
            [
                str(run_dir / "snapshots" / "snapshot_edges.parquet"),
                str(run_dir / "snapshots" / "observer_profiles.parquet"),
                bucket,
            ],
        ).fetchall()
        if str(row[0]) in by_id
    ]
    if node_count > len(available_ids):
        raise ValueError(
            f"node_count {node_count} exceeds {len(available_ids)} bounded-observer "
            f"peers available at initial bucket {bucket}"
        )
    randomizer = random.Random(seed)
    selected_ids = available_ids if node_count == len(available_ids) else randomizer.sample(available_ids, node_count)
    return (
        [by_id[node_id] for node_id in sorted(selected_ids)],
        bucket,
        [by_id[node_id] for node_id in available_ids],
    )


def _calibrate_intercept(scores: np.ndarray, target_probability: float) -> float:
    target = _clip_probability(target_probability)
    low, high = -40.0, 40.0
    for _ in range(80):
        midpoint = (low + high) / 2.0
        if float(np.mean(_sigmoid_array(scores + midpoint))) < target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def _connect_components(
    node_count: int,
    edges: set[tuple[int, int]],
    probabilities: dict[tuple[int, int], float],
    safe_peer_limit: int,
) -> int:
    added = 0
    while True:
        components = _components(node_count, edges)
        if len(components) <= 1:
            return added
        degree = _degrees(node_count, edges)
        component_of = {
            node: component_index
            for component_index, component in enumerate(components)
            for node in component
        }
        candidates = [
            (probability, edge)
            for edge, probability in probabilities.items()
            if edge not in edges
            and component_of[edge[0]] != component_of[edge[1]]
            and degree[edge[0]] < safe_peer_limit
            and degree[edge[1]] < safe_peer_limit
        ]
        if not candidates:
            raise RuntimeError("cannot connect graph without violating the safe peer limit")
        _, edge = max(candidates, key=lambda item: (item[0], item[1]))
        edges.add(edge)
        added += 1


def _enforce_peer_limit(
    node_count: int,
    edges: set[tuple[int, int]],
    probabilities: dict[tuple[int, int], float],
    safe_peer_limit: int,
) -> int:
    removed = 0
    while max(_degrees(node_count, edges), default=0) > safe_peer_limit:
        degree = _degrees(node_count, edges)
        overloaded = max(range(node_count), key=lambda node: degree[node])
        candidates = sorted(
            (edge for edge in edges if overloaded in edge),
            key=lambda edge: probabilities.get(edge, 0.0),
        )
        for edge in candidates:
            edges.remove(edge)
            if _is_connected(node_count, edges):
                removed += 1
                break
            edges.add(edge)
        else:
            raise RuntimeError("cannot enforce the peer limit while preserving connectivity")
    return removed


def _pair_scores(
    selected: list[NodeProfile],
    formation_coefficients: np.ndarray,
    retention_coefficients: np.ndarray,
) -> tuple[list[tuple[int, int]], np.ndarray, np.ndarray, list[np.ndarray]]:
    pairs: list[tuple[int, int]] = []
    formation_scores: list[float] = []
    retention_scores: list[float] = []
    features: list[np.ndarray] = []
    retention_pair_coefficients = retention_coefficients[1:]
    for left in range(len(selected)):
        for right in range(left + 1, len(selected)):
            pair_features = _pair_features(selected[left], selected[right])
            pairs.append((left, right))
            features.append(pair_features)
            formation_scores.append(
                selected[left].q
                + selected[right].q
                + float(pair_features @ formation_coefficients)
            )
            retention_scores.append(
                0.5 * (selected[left].r + selected[right].r)
                + float(pair_features @ retention_pair_coefficients)
            )
    return (
        pairs,
        np.asarray(formation_scores),
        np.asarray(retention_scores),
        features,
    )


def _generate_initial_graph(
    node_count: int,
    pairs: list[tuple[int, int]],
    scores: np.ndarray,
    mean_degree_min: float,
    mean_degree_max: float,
    safe_peer_limit: int,
    seed: int,
) -> tuple[set[tuple[int, int]], dict[tuple[int, int], float], dict[str, Any]]:
    expected_degree = (mean_degree_min + mean_degree_max) / 2.0
    intercept = _calibrate_intercept(scores, expected_degree / (node_count - 1))
    probability_values = _sigmoid_array(scores + intercept)
    probabilities = dict(zip(pairs, map(float, probability_values)))
    best: tuple[float, set[tuple[int, int]], int, int] | None = None
    for attempt in range(20):
        randomizer = random.Random(seed + attempt * 1009)
        edges = {
            edge for edge, probability in probabilities.items()
            if randomizer.random() < probability
        }
        repaired = _connect_components(node_count, edges, probabilities, safe_peer_limit)
        removed = _enforce_peer_limit(node_count, edges, probabilities, safe_peer_limit)
        mean_degree = 2.0 * len(edges) / node_count
        distance = max(mean_degree_min - mean_degree, 0.0, mean_degree - mean_degree_max)
        candidate = (distance, edges, repaired, removed)
        if best is None or candidate[0] < best[0]:
            best = candidate
        if distance == 0.0:
            break
    assert best is not None
    return best[1], probabilities, {
        "probability_intercept": intercept,
        "expected_mean_degree": expected_degree,
        "sampled_mean_degree": 2.0 * len(best[1]) / node_count,
        "connectivity_repair_edges": best[2],
        "peer_limit_removals": best[3],
        "density_is_range_not_exact_target": True,
    }


def _evolve_graphs(
    node_count: int,
    initial: set[tuple[int, int]],
    pairs: list[tuple[int, int]],
    formation_scores: np.ndarray,
    retention_scores: np.ndarray,
    observed_retention: float,
    observed_formation_ratio: float,
    safe_peer_limit: int,
    snapshot_count: int,
    seed: int,
) -> tuple[list[set[tuple[int, int]]], list[dict[str, Any]], dict[str, Any]]:
    retention_score_by_pair = dict(zip(pairs, map(float, retention_scores)))
    formation_score_by_pair = dict(zip(pairs, map(float, formation_scores)))
    snapshots = [set(initial)]
    transitions: list[dict[str, Any]] = []
    retention_intercepts: list[float] = []
    for snapshot_index in range(1, snapshot_count + 1):
        previous = snapshots[-1]
        randomizer = random.Random(seed + snapshot_index * 7919)
        current_retention_scores = np.asarray(
            [retention_score_by_pair[edge] for edge in previous]
        )
        retention_intercept = _calibrate_intercept(
            current_retention_scores, observed_retention
        )
        retention_intercepts.append(retention_intercept)
        retention_probability = {
            edge: float(_sigmoid_array(
                np.asarray([retention_score_by_pair[edge] + retention_intercept])
            )[0])
            for edge in previous
        }
        surviving = {
            edge for edge in previous
            if randomizer.random() < retention_probability[edge]
        }
        nonedges = [edge for edge in pairs if edge not in previous]
        target_additions = observed_formation_ratio * len(previous)
        if nonedges and target_additions > 0:
            values = np.asarray([formation_score_by_pair[edge] for edge in nonedges])
            formation_intercept = _calibrate_intercept(
                values, min(0.95, target_additions / len(nonedges))
            )
            formation_probabilities = _sigmoid_array(values + formation_intercept)
        else:
            formation_intercept = -40.0
            formation_probabilities = np.zeros(len(nonedges))
        degree = _degrees(node_count, surviving)
        formed: set[tuple[int, int]] = set()
        ranked = list(zip(nonedges, map(float, formation_probabilities)))
        randomizer.shuffle(ranked)
        for edge, probability in ranked:
            if degree[edge[0]] >= safe_peer_limit or degree[edge[1]] >= safe_peer_limit:
                continue
            if randomizer.random() < probability:
                formed.add(edge)
                degree[edge[0]] += 1
                degree[edge[1]] += 1
        current = surviving | formed
        all_probabilities = {
            edge: formation_score_by_pair[edge] for edge in pairs
        }
        repaired = _connect_components(
            node_count, current, all_probabilities, safe_peer_limit
        )
        removed = _enforce_peer_limit(
            node_count, current, all_probabilities, safe_peer_limit
        )
        retained_count = len(previous & current)
        formed_count = len(current - previous)
        dissolved_count = len(previous - current)
        transitions.append(
            {
                "from_snapshot": snapshot_index - 1,
                "to_snapshot": snapshot_index,
                "previous_edges": len(previous),
                "current_edges": len(current),
                "retained_edges": retained_count,
                "formed_edges": formed_count,
                "dissolved_edges": dissolved_count,
                "retention_fraction": retained_count / len(previous) if previous else 1.0,
                "formation_fraction": formed_count / len(previous) if previous else 0.0,
                "jaccard": len(previous & current) / len(previous | current),
                "formation_probability_intercept": formation_intercept,
                "retention_probability_intercept": retention_intercept,
                "connectivity_repair_edges": repaired,
                "peer_limit_removals": removed,
            }
        )
        snapshots.append(current)
    return snapshots, transitions, {
        "retention_probability_intercepts": retention_intercepts,
        "observed_retention_target": observed_retention,
        "observed_formation_target": observed_formation_ratio,
    }


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with temporary.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)
    temporary.replace(path)


def _csv_to_parquet(
    connection: Any,
    csv_path: Path,
    parquet_path: Path,
    columns: dict[str, str],
) -> None:
    temporary = parquet_path.with_name(f".{parquet_path.name}.tmp-{os.getpid()}")
    declaration = "{" + ",".join(
        f"{sql_string(name)}:{sql_string(data_type)}" for name, data_type in columns.items()
    ) + "}"
    connection.execute(
        f"COPY (SELECT * FROM read_csv({sql_string(csv_path)}, header=true, "
        f"columns={declaration})) TO {sql_string(temporary)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    temporary.replace(parquet_path)


def _write_outputs(
    connection: Any,
    output_dir: Path,
    model_id: str,
    selected: list[NodeProfile],
    snapshots: list[set[tuple[int, int]]],
    transitions: list[dict[str, Any]],
    step_minutes: int,
    formation_probabilities: dict[tuple[int, int], float],
) -> None:
    node_header = (
        "node_index", "node_id", "client", "asn", "as_org", "country", "city",
        "availability", "visibility", "formations", "retention_exposures", "retained",
        "sessions", "session_exposure_ms", "observed_ends", "duration_p50_ms", "q", "r",
    )
    node_rows = [
        (
            index, profile.node_id, profile.client, profile.asn, profile.as_org,
            profile.country, profile.city, profile.availability, profile.visibility,
            profile.formations, profile.retention_exposures, profile.retained,
            profile.sessions, profile.session_exposure_ms, profile.observed_ends,
            profile.duration_p50_ms, profile.q, profile.r,
        )
        for index, profile in enumerate(selected)
    ]
    nodes_csv = output_dir / "nodes.csv"
    _write_csv(nodes_csv, node_header, node_rows)
    _csv_to_parquet(
        connection,
        nodes_csv,
        output_dir / "nodes.parquet",
        {
            "node_index": "INTEGER", "node_id": "VARCHAR", "client": "VARCHAR",
            "asn": "VARCHAR", "as_org": "VARCHAR", "country": "VARCHAR",
            "city": "VARCHAR", "availability": "DOUBLE", "visibility": "DOUBLE",
            "formations": "BIGINT", "retention_exposures": "BIGINT", "retained": "BIGINT",
            "sessions": "BIGINT", "session_exposure_ms": "DOUBLE",
            "observed_ends": "BIGINT", "duration_p50_ms": "DOUBLE", "q": "DOUBLE", "r": "DOUBLE",
        },
    )

    all_rows: list[tuple[Any, ...]] = []
    for snapshot_index, edges in enumerate(snapshots):
        rows = [
            (
                snapshot_index,
                snapshot_index * step_minutes,
                selected[left].node_id,
                selected[right].node_id,
                formation_probabilities.get((left, right), 0.0),
                True,
                model_id,
            )
            for left, right in sorted(edges)
        ]
        all_rows.extend(rows)
        _write_csv(
            output_dir / f"snapshot_{snapshot_index}.csv",
            (
                "snapshot_index", "elapsed_minutes", "source", "target",
                "initial_edge_probability", "synthetic", "model_id",
            ),
            rows,
        )
    snapshots_csv = output_dir / "snapshots.csv"
    _write_csv(
        snapshots_csv,
        (
            "snapshot_index", "elapsed_minutes", "source", "target",
            "initial_edge_probability", "synthetic", "model_id",
        ),
        all_rows,
    )
    _csv_to_parquet(
        connection,
        snapshots_csv,
        output_dir / "snapshots.parquet",
        {
            "snapshot_index": "INTEGER", "elapsed_minutes": "INTEGER",
            "source": "VARCHAR", "target": "VARCHAR",
            "initial_edge_probability": "DOUBLE", "synthetic": "BOOLEAN",
            "model_id": "VARCHAR",
        },
    )
    event_rows: list[tuple[Any, ...]] = []
    for transition, previous, current in zip(transitions, snapshots, snapshots[1:]):
        for left, right in sorted(previous - current):
            event_rows.append(
                (transition["to_snapshot"], transition["to_snapshot"] * step_minutes,
                 "dissolved", selected[left].node_id, selected[right].node_id)
            )
        for left, right in sorted(current - previous):
            event_rows.append(
                (transition["to_snapshot"], transition["to_snapshot"] * step_minutes,
                 "formed", selected[left].node_id, selected[right].node_id)
            )
    events_csv = output_dir / "edge_events.csv"
    _write_csv(
        events_csv,
        ("snapshot_index", "elapsed_minutes", "event", "source", "target"),
        event_rows,
    )
    _csv_to_parquet(
        connection,
        events_csv,
        output_dir / "edge_events.parquet",
        {
            "snapshot_index": "INTEGER", "elapsed_minutes": "INTEGER",
            "event": "VARCHAR", "source": "VARCHAR", "target": "VARCHAR",
        },
    )


def _distribution(values: Iterable[str]) -> dict[str, float]:
    counts = Counter(values)
    total = sum(counts.values())
    return {key: value / total for key, value in sorted(counts.items())} if total else {}


def _total_variation(left: dict[str, float], right: dict[str, float]) -> float:
    keys = set(left) | set(right)
    return 0.5 * sum(abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in keys)


def _model_id(
    layout: DatasetLayout,
    run_dir: Path,
    parameters: dict[str, Any],
) -> str:
    manifest = read_json(layout.manifest_path)
    payload = {
        "algorithm": MODEL_ALGORITHM,
        "dataset_id": layout.dataset_id,
        "run_id": run_dir.name,
        "raw_sha256": {
            name: value["sha256"] for name, value in manifest["raw_files"].items()
        },
        "parameters": parameters,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    return f"{MODEL_ALGORITHM}-{digest}"


def fit_topology(
    layout: DatasetLayout,
    run_dir: Path,
    *,
    node_count: int = 300,
    snapshot_count: int = 5,
    step_minutes: int = 10,
    mean_degree_min: float = 6.0,
    mean_degree_max: float = 24.0,
    peer_limit: int = 80,
    peer_reserve: int = 16,
    sample_limit: int = 50_000,
    seed: int = 1337,
) -> Path:
    if node_count < 3:
        raise ValueError("node_count must be at least 3")
    if snapshot_count != 5:
        raise ValueError("snapshot_count must be 5 for the agreed model artifact")
    if step_minutes < 1:
        raise ValueError("step_minutes must be at least 1")
    if not 0 < mean_degree_min <= mean_degree_max < node_count:
        raise ValueError("mean-degree range must be positive and below node_count")
    safe_peer_limit = peer_limit - peer_reserve
    if safe_peer_limit < 2:
        raise ValueError("peer_limit - peer_reserve must be at least 2")
    if mean_degree_max >= safe_peer_limit:
        raise ValueError("mean_degree_max must remain below the safe peer limit")
    if sample_limit < 100:
        raise ValueError("sample_limit must be at least 100")

    snapshot_dir = run_dir / "snapshots"
    for required in (
        snapshot_dir / "snapshot_edges.parquet",
        snapshot_dir / "observer_profiles.parquet",
        snapshot_dir / "heartbeat_sessions.parquet",
        snapshot_dir / "data_quality_report.json",
    ):
        if not required.exists():
            raise RuntimeError(f"required topology input is missing: {required}")

    parameters = {
        "node_count": node_count,
        "snapshot_count": snapshot_count,
        "step_minutes": step_minutes,
        "mean_degree_range": [mean_degree_min, mean_degree_max],
        "peer_limit": peer_limit,
        "peer_reserve": peer_reserve,
        "safe_peer_limit": safe_peer_limit,
        "sample_limit": sample_limit,
        "seed": seed,
    }
    model_id = _model_id(layout, run_dir, parameters)
    output_dir = layout.path / "topology_models" / model_id
    required_outputs = (
        output_dir / "model.json",
        output_dir / "validation_report.json",
        output_dir / "nodes.parquet",
        output_dir / "snapshots.parquet",
        output_dir / "edge_events.parquet",
        *(output_dir / f"snapshot_{index}.csv" for index in range(snapshot_count + 1)),
    )
    if all(path.exists() for path in required_outputs):
        return output_dir

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{model_id}.tmp-", dir=output_dir.parent)
    )
    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        connection.execute("SET preserve_insertion_order=false")
        profiles, observed = _load_node_profiles(connection, run_dir, step_minutes)
        formation_coefficients, retention_coefficients, pair_fit = _fit_pair_models(
            connection, run_dir, profiles, step_minutes, sample_limit, seed
        )
        selected, initial_bucket, initial_candidates = _select_nodes(
            connection, run_dir, profiles, node_count, seed
        )
        pairs, formation_scores, retention_scores, _ = _pair_scores(
            selected, formation_coefficients, retention_coefficients
        )
        initial, initial_probabilities, initial_generation = _generate_initial_graph(
            node_count,
            pairs,
            formation_scores + retention_scores,
            mean_degree_min,
            mean_degree_max,
            safe_peer_limit,
            seed,
        )
        observed_formation_ratio = min(
            0.95,
            observed["formation_events"] / max(1, observed["retention_exposures"]),
        )
        snapshots, transitions, churn_calibration = _evolve_graphs(
            node_count,
            initial,
            pairs,
            formation_scores,
            retention_scores,
            observed["global_retention"],
            observed_formation_ratio,
            safe_peer_limit,
            snapshot_count,
            seed,
        )
        _write_outputs(
            connection,
            temporary_dir,
            model_id,
            selected,
            snapshots,
            transitions,
            step_minutes,
            initial_probabilities,
        )
    finally:
        connection.close()

    snapshot_statistics: list[dict[str, Any]] = []
    structural_valid = True
    for index, edges in enumerate(snapshots):
        degree = _degrees(node_count, edges)
        components = _components(node_count, edges)
        statistics = {
            "snapshot_index": index,
            "elapsed_minutes": index * step_minutes,
            "nodes": node_count,
            "edges": len(edges),
            "components": len(components),
            "isolated_nodes": sum(value == 0 for value in degree),
            "mean_degree": 2.0 * len(edges) / node_count,
            "degree_quantiles": _quantiles([float(value) for value in degree]),
            "maximum_degree": max(degree),
        }
        snapshot_statistics.append(statistics)
        structural_valid &= (
            statistics["components"] == 1
            and statistics["isolated_nodes"] == 0
            and statistics["maximum_degree"] <= safe_peer_limit
        )

    generated_retention = float(
        np.mean([transition["retention_fraction"] for transition in transitions])
    )
    generated_formation = float(
        np.mean([transition["formation_fraction"] for transition in transitions])
    )
    retention_error = abs(generated_retention - observed["global_retention"])
    formation_error = abs(generated_formation - observed_formation_ratio)
    source_client = _distribution(profile.client for profile in initial_candidates)
    selected_client = _distribution(profile.client for profile in selected)
    source_country = _distribution(profile.country for profile in initial_candidates)
    selected_country = _distribution(profile.country for profile in selected)
    client_tv = _total_variation(source_client, selected_client)
    country_tv = _total_variation(source_country, selected_country)
    transition_changes = all(
        transition["formed_edges"] > 0 and transition["dissolved_edges"] > 0
        for transition in transitions
    )
    churn_evaluable = node_count >= 10 and len(initial) < len(pairs)
    density_valid = all(
        mean_degree_min <= statistic["mean_degree"] <= mean_degree_max
        for statistic in snapshot_statistics
    )
    validation = {
        "schema_version": 1,
        "model_id": model_id,
        "generated_at": utc_now(),
        "valid": bool(
            structural_valid
            and density_valid
            and (not churn_evaluable or retention_error <= 0.08)
            and (not churn_evaluable or formation_error <= 0.08)
            and (not churn_evaluable or transition_changes)
        ),
        "claim": (
            "The output is a synthetic remote-peer topology matching measurable "
            "Xatu observer formation and retention patterns; it is not recovered "
            "Ethereum Mainnet ground-truth adjacency."
        ),
        "structural_constraints": {
            "valid": structural_valid,
            "density_range_valid": density_valid,
            "safe_peer_limit": safe_peer_limit,
            "no_per_node_degree_target": True,
        },
        "snapshots": snapshot_statistics,
        "transitions": transitions,
        "xatu_pattern_checks": {
            "observed_retention": observed["global_retention"],
            "generated_retention": generated_retention,
            "retention_absolute_error": retention_error,
            "observed_formation_fraction": observed_formation_ratio,
            "generated_formation_fraction": generated_formation,
            "formation_absolute_error": formation_error,
            "all_five_transitions_change_edges": transition_changes,
            "churn_pattern_evaluable": churn_evaluable,
            "selected_client_total_variation": client_tv,
            "selected_country_total_variation": country_tv,
            "initial_candidate_nodes": len(initial_candidates),
        },
        "pair_model_held_out_checks": pair_fit,
        "limitations": [
            "Remote-to-remote edges are latent and cannot be directly validated in Xatu.",
            "Absolute hidden graph density is externally bounded rather than learned.",
            "Pair constraints are retained only when their sign is time-stable and they improve held-out loss.",
            "The current artifact is fitted from one complete Xatu day.",
        ],
    }
    atomic_write_json(temporary_dir / "validation_report.json", validation)

    model = {
        "schema_version": 1,
        "model_id": model_id,
        "algorithm": MODEL_ALGORITHM,
        "generated_at": utc_now(),
        "dataset_id": layout.dataset_id,
        "source_run_id": run_dir.name,
        "initial_xatu_bucket": initial_bucket,
        "parameters": parameters,
        "observed_calibration": observed,
        "node_effects": {
            "formation_q": "empirical-Bayes new-edge propensity with ridge partial pooling",
            "persistence_r": "empirical-Bayes edge retention with ridge partial pooling",
            "observer_degree_used_as_remote_degree": False,
        },
        "pair_features": list(PAIR_FEATURES),
        "formation_coefficients": {
            name: float(value) for name, value in zip(PAIR_FEATURES, formation_coefficients)
        },
        "retention_coefficients": {
            "intercept": float(retention_coefficients[0]),
            **{
                name: float(value)
                for name, value in zip(PAIR_FEATURES, retention_coefficients[1:])
            },
        },
        "pair_fit": pair_fit,
        "initial_generation": initial_generation,
        "churn_calibration": churn_calibration,
        "semantics": {
            "snapshot_0": "initial synthetic remote-to-remote graph",
            "snapshot_1_to_5": "ten-minute edge churn steps by default",
            "node_identity": "sampled Xatu remote peer attributes used as synthetic node profiles",
            "edge_identity": "synthetic and not claimed as an observed Mainnet remote edge",
        },
        "outputs": [
            "nodes.csv", "nodes.parquet", "snapshots.csv", "snapshots.parquet",
            "edge_events.csv", "edge_events.parquet", "snapshot_0.csv",
            "snapshot_1.csv", "snapshot_2.csv", "snapshot_3.csv",
            "snapshot_4.csv", "snapshot_5.csv", "validation_report.json",
        ],
    }
    atomic_write_json(temporary_dir / "model.json", model)
    if not validation["valid"]:
        atomic_write_json(temporary_dir / "FAILED_VALIDATION.json", validation)
        raise RuntimeError(
            f"generated topology failed validation; diagnostics retained at {temporary_dir}"
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir.replace(output_dir)
    return output_dir
