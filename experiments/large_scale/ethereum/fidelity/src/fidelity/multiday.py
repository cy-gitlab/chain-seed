from __future__ import annotations

import hashlib
import json
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .normalize import require_duckdb
from .storage import DatasetLayout, atomic_write_json, read_json, utc_now
from .topology import (
    PAIR_FEATURES,
    NodeProfile,
    _binary_log_loss,
    _components,
    _degrees,
    _evolve_graphs,
    _fit_logistic,
    _fit_profile_effects,
    _formation_samples,
    _generate_initial_graph,
    _load_node_profiles,
    _observer_profiles,
    _pair_features,
    _pair_scores,
    _predict_logistic,
    _quantiles,
    _retention_samples,
    _ridge_node_effects,
    _select_nodes,
    _write_csv,
    _write_outputs,
)
from .topology_prep import latest_topology_input


MULTIDAY_ALGORITHM = "xatu-remote-propensity-multiday-v2"


@dataclass(frozen=True)
class DayInput:
    date: str
    layout: DatasetLayout
    run_dir: Path


@dataclass
class PairData:
    features: np.ndarray
    outcomes: np.ndarray
    offsets: np.ndarray
    days: np.ndarray
    known_fraction: float


def _empty_pair_data() -> PairData:
    return PairData(
        np.empty((0, len(PAIR_FEATURES)), dtype=float),
        np.empty(0, dtype=float),
        np.empty(0, dtype=float),
        np.empty(0, dtype=str),
        0.0,
    )


def _dataset_date(layout: DatasetLayout) -> str:
    manifest = read_json(layout.manifest_path)
    value = manifest.get("date")
    if not value:
        raise RuntimeError(f"dataset has no date in manifest: {layout.path}")
    return str(value)


def resolve_days(layouts: Sequence[DatasetLayout]) -> list[DayInput]:
    days = [
        DayInput(_dataset_date(layout), layout, latest_topology_input(layout))
        for layout in layouts
    ]
    ordered = sorted(days, key=lambda item: item.date)
    if len({item.date for item in ordered}) != len(ordered):
        raise ValueError("dataset dates must be unique")
    return ordered


def _stable_observers(
    connection: Any,
    days: Sequence[DayInput],
    minimum_bounded_fraction: float = 0.8,
) -> tuple[list[str], dict[str, Any]]:
    states: dict[str, dict[str, int]] = {}
    for day in days:
        rows = connection.execute(
            "SELECT source, behavior_cohort FROM read_parquet(?)",
            [str(day.run_dir / "snapshots" / "observer_profiles.parquet")],
        ).fetchall()
        for source, cohort in rows:
            counts = states.setdefault(
                str(source), {"seen": 0, "bounded": 0, "bursty": 0, "bulk": 0}
            )
            counts["seen"] += 1
            if cohort == "bounded":
                counts["bounded"] += 1
            elif cohort == "bursty-probe":
                counts["bursty"] += 1
            elif cohort == "bulk-probe":
                counts["bulk"] += 1
    required_days = max(1, math.ceil(len(days) * minimum_bounded_fraction))
    selected = sorted(
        source
        for source, counts in states.items()
        if counts["bounded"] >= required_days and counts["bulk"] == 0
    )
    if len(selected) < 2:
        raise RuntimeError("fewer than two stable bounded training observers remain")
    return selected, {
        "fit_dates": [day.date for day in days],
        "minimum_bounded_fraction": minimum_bounded_fraction,
        "minimum_bounded_days": required_days,
        "bulk_probe_days_allowed": 0,
        "observed_identities": len(states),
        "selected_identities": len(selected),
        "selected_observers": selected,
    }


def _aggregate_profiles(
    connection: Any,
    days: Sequence[DayInput],
    step_minutes: int,
    *,
    fit_node_effects: bool = True,
    observer_ids: Sequence[str] | None = None,
) -> tuple[list[NodeProfile], dict[str, Any], list[dict[str, Any]]]:
    if not days:
        raise ValueError("at least one day is required for profile fitting")
    accumulated: dict[str, dict[str, Any]] = {}
    daily_observed: list[dict[str, Any]] = []
    for day in days:
        profiles, observed = _load_node_profiles(
            connection,
            day.run_dir,
            step_minutes,
            fit_effects=False,
            observer_ids=observer_ids,
        )
        daily_observed.append({"date": day.date, **observed})
        for profile in profiles:
            row = accumulated.get(profile.node_id)
            if row is None:
                row = {
                    "profile": profile,
                    "availability_numerator": 0.0,
                    "availability_denominator": 0.0,
                    "visibility_numerator": 0.0,
                    "visibility_denominator": 0.0,
                    "duration_numerator": 0.0,
                }
                accumulated[profile.node_id] = row
            base = row["profile"]
            for attribute in ("client", "asn", "as_org", "country", "city"):
                value = getattr(profile, attribute)
                if value:
                    setattr(base, attribute, value)
            buckets = float(observed["bucket_count"])
            observers = float(observed["bounded_observer_count"])
            row["availability_numerator"] += profile.availability * buckets
            row["availability_denominator"] += buckets
            row["visibility_numerator"] += profile.visibility * observers
            row["visibility_denominator"] += observers
            row["duration_numerator"] += profile.duration_p50_ms * max(1, profile.sessions)
            base.formations += 0 if base is profile else profile.formations
            base.retention_exposures += (
                0 if base is profile else profile.retention_exposures
            )
            base.retained += 0 if base is profile else profile.retained
            base.sessions += 0 if base is profile else profile.sessions
            base.session_exposure_ms += (
                0.0 if base is profile else profile.session_exposure_ms
            )
            base.observed_ends += 0 if base is profile else profile.observed_ends
            base.formation_opportunities += (
                0.0 if base is profile else profile.formation_opportunities
            )

    profiles = []
    for node_id in sorted(accumulated):
        row = accumulated[node_id]
        profile = row["profile"]
        profile.availability = row["availability_numerator"] / max(
            1.0, row["availability_denominator"]
        )
        profile.visibility = row["visibility_numerator"] / max(
            1.0, row["visibility_denominator"]
        )
        profile.duration_p50_ms = row["duration_numerator"] / max(
            1, profile.sessions
        )
        profiles.append(profile)

    total_opportunities = sum(profile.formation_opportunities for profile in profiles)
    formations = sum(profile.formations for profile in profiles)
    retention_exposures = sum(profile.retention_exposures for profile in profiles)
    retained = sum(profile.retained for profile in profiles)
    global_formation = formations / max(1.0, total_opportunities)
    global_retention = retained / max(1, retention_exposures)
    ridge = None
    if fit_node_effects:
        _fit_profile_effects(profiles, global_formation, global_retention)
        ridge = _ridge_node_effects(profiles)
    observed = {
        "dates": [day.date for day in days],
        "day_count": len(days),
        "remote_peer_count": len(profiles),
        "formation_opportunities": total_opportunities,
        "formation_events": formations,
        "retention_exposures": retention_exposures,
        "retained_edges": retained,
        "global_formation_rate_per_opportunity": global_formation,
        "global_retention": global_retention,
        "global_formation_fraction_per_active_edge": formations
        / max(1, retention_exposures),
        "ridge": ridge,
    }
    return profiles, observed, daily_observed


def _remote_metadata(connection: Any, run_dir: Path) -> dict[str, NodeProfile]:
    rows = connection.execute(
        """
        SELECT node_id, client, asn, as_org, country_code, city
        FROM read_parquet(?) WHERE node_role='remote'
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
            availability=0.0,
            visibility=0.0,
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


def _project_effects(
    metadata: dict[str, NodeProfile], learned: dict[str, NodeProfile]
) -> None:
    for node_id, profile in metadata.items():
        fitted = learned.get(node_id)
        if fitted is not None:
            profile.q = fitted.q
            profile.r = fitted.r


def _collect_pair_data(
    connection: Any,
    days: Sequence[DayInput],
    learned_profiles: Sequence[NodeProfile],
    step_minutes: int,
    samples_per_day: int,
    seed: int,
    kind: str,
    observer_ids: Sequence[str] | None = None,
) -> PairData:
    learned = {profile.node_id: profile for profile in learned_profiles}
    features: list[np.ndarray] = []
    outcomes: list[float] = []
    offsets: list[float] = []
    labels: list[str] = []
    known_endpoints = 0
    total_endpoints = 0
    for day_index, day in enumerate(days):
        metadata = _remote_metadata(connection, day.run_dir)
        _project_effects(metadata, learned)
        observers = _observer_profiles(connection, day.run_dir)
        day_seed = seed + day_index * 1009
        if kind == "formation":
            rows = _formation_samples(
                connection,
                day.run_dir,
                step_minutes,
                samples_per_day,
                day_seed,
                observer_ids,
            )
            for _, source, positive_id, negative_id in rows:
                observer = observers.get(str(source))
                positive = metadata.get(str(positive_id))
                negative = metadata.get(str(negative_id))
                if observer is None or positive is None or negative is None:
                    continue
                features.append(
                    _pair_features(observer, positive)
                    - _pair_features(observer, negative)
                )
                offsets.append(positive.q - negative.q)
                outcomes.append(1.0)
                labels.append(day.date)
                known_endpoints += int(str(positive_id) in learned)
                known_endpoints += int(str(negative_id) in learned)
                total_endpoints += 2
        elif kind == "retention":
            rows = _retention_samples(
                connection,
                day.run_dir,
                step_minutes,
                samples_per_day,
                day_seed,
                observer_ids,
            )
            for _, source, target_id, retained in rows:
                observer = observers.get(str(source))
                target = metadata.get(str(target_id))
                if observer is None or target is None:
                    continue
                features.append(_pair_features(observer, target))
                offsets.append(target.r)
                outcomes.append(float(retained))
                labels.append(day.date)
                known_endpoints += int(str(target_id) in learned)
                total_endpoints += 1
        else:
            raise ValueError(f"unsupported pair sample kind: {kind}")
    if not features:
        return _empty_pair_data()
    return PairData(
        np.asarray(features, dtype=float).reshape((-1, len(PAIR_FEATURES))),
        np.asarray(outcomes, dtype=float),
        np.asarray(offsets, dtype=float),
        np.asarray(labels, dtype=str),
        known_endpoints / max(1, total_endpoints),
    )


def _merge_pair_data(parts: Sequence[PairData]) -> PairData:
    nonempty = [part for part in parts if len(part.outcomes)]
    if not nonempty:
        return _empty_pair_data()
    total = sum(len(part.outcomes) for part in nonempty)
    return PairData(
        np.concatenate([part.features for part in nonempty]),
        np.concatenate([part.outcomes for part in nonempty]),
        np.concatenate([part.offsets for part in nonempty]),
        np.concatenate([part.days for part in nonempty]),
        sum(part.known_fraction * len(part.outcomes) for part in nonempty) / total,
    )


def _auc(outcomes: np.ndarray, probabilities: np.ndarray) -> float | None:
    positive = outcomes == 1
    negative = outcomes == 0
    positive_count = int(np.sum(positive))
    negative_count = int(np.sum(negative))
    if not positive_count or not negative_count:
        return None
    order = np.argsort(probabilities, kind="mergesort")
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1)
    unique, inverse, counts = np.unique(
        probabilities, return_inverse=True, return_counts=True
    )
    if len(unique) != len(probabilities):
        for index, count in enumerate(counts):
            if count > 1:
                positions = np.where(inverse == index)[0]
                ranks[positions] = float(np.mean(ranks[positions]))
    rank_sum = float(np.sum(ranks[positive]))
    return (
        rank_sum - positive_count * (positive_count + 1) / 2
    ) / (positive_count * negative_count)


def _metrics(
    data: PairData,
    coefficients: np.ndarray,
    retained: Sequence[int],
    include_intercept: bool,
) -> dict[str, Any]:
    selected = data.features[:, retained] if retained else np.empty((len(data.outcomes), 0))
    compact = (
        np.concatenate(
            ([coefficients[0]], [coefficients[1 + index] for index in retained])
        )
        if include_intercept
        else np.asarray([coefficients[index] for index in retained], dtype=float)
    )
    probability = _predict_logistic(
        selected, data.offsets, compact, include_intercept
    )
    return {
        "samples": int(len(data.outcomes)),
        "known_endpoint_fraction": data.known_fraction,
        "log_loss": _binary_log_loss(data.outcomes, probability),
        "brier_score": float(np.mean((probability - data.outcomes) ** 2)),
        "accuracy": float(np.mean((probability >= 0.5) == data.outcomes)),
        "auc": _auc(data.outcomes, probability),
    }


def _fit_full_coefficients(
    data: PairData,
    retained: Sequence[int],
    include_intercept: bool,
) -> np.ndarray:
    selected = data.features[:, retained] if retained else np.empty((len(data.outcomes), 0))
    compact = _fit_logistic(
        selected,
        data.outcomes,
        data.offsets,
        include_intercept=include_intercept,
    )
    output = np.zeros(len(PAIR_FEATURES) + int(include_intercept), dtype=float)
    if include_intercept:
        output[0] = compact[0]
        for position, feature_index in enumerate(retained, start=1):
            output[1 + feature_index] = compact[position]
    else:
        for position, feature_index in enumerate(retained):
            output[feature_index] = compact[position]
    return output


def _select_features(
    train: PairData,
    validation: PairData,
    *,
    include_intercept: bool,
) -> tuple[list[int], dict[str, Any]]:
    if len(train.outcomes) < 40 or len(validation.outcomes) < 20:
        return [], {"status": "insufficient-samples", "retained_features": []}
    baseline = _fit_full_coefficients(train, [], include_intercept)
    baseline_metrics = _metrics(validation, baseline, [], include_intercept)
    unique_days = sorted(set(train.days.tolist()))
    split = max(1, len(unique_days) // 2)
    first_days = set(unique_days[:split])
    first_mask = np.asarray([day in first_days for day in train.days])
    second_mask = ~first_mask
    stable: dict[int, bool] = {}
    signs: dict[int, list[float]] = {}
    for index in range(len(PAIR_FEATURES)):
        values = []
        for mask in (first_mask, second_mask):
            subset = PairData(
                train.features[mask],
                train.outcomes[mask],
                train.offsets[mask],
                train.days[mask],
                train.known_fraction,
            )
            coefficient = _fit_full_coefficients(
                subset, [index], include_intercept
            )[index + int(include_intercept)]
            values.append(float(coefficient))
        signs[index] = values
        stable[index] = bool(
            abs(values[0]) >= 0.02
            and abs(values[1]) >= 0.02
            and values[0] * values[1] > 0
        )

    retained: list[int] = []
    current_loss = float(baseline_metrics["log_loss"])
    while True:
        best: tuple[float, int] | None = None
        for index in range(len(PAIR_FEATURES)):
            if index in retained or not stable[index]:
                continue
            candidate = retained + [index]
            coefficients = _fit_full_coefficients(
                train, candidate, include_intercept
            )
            loss = float(
                _metrics(validation, coefficients, candidate, include_intercept)[
                    "log_loss"
                ]
            )
            if best is None or loss < best[0]:
                best = (loss, index)
        if best is None or current_loss - best[0] < 5e-4:
            break
        current_loss = best[0]
        retained.append(best[1])
    return retained, {
        "status": "fit",
        "train_dates": unique_days,
        "validation_dates": sorted(set(validation.days.tolist())),
        "baseline_validation": baseline_metrics,
        "selected_validation_log_loss": current_loss,
        "minimum_log_loss_improvement": 5e-4,
        "retained_features": [PAIR_FEATURES[index] for index in retained],
        "dropped_features": [
            name for index, name in enumerate(PAIR_FEATURES) if index not in retained
        ],
        "half_fit_coefficients": {
            PAIR_FEATURES[index]: signs[index] for index in range(len(PAIR_FEATURES))
        },
        "stable_sign": {
            PAIR_FEATURES[index]: stable[index] for index in range(len(PAIR_FEATURES))
        },
    }


def _fit_and_test(
    train: PairData,
    validation: PairData,
    refit: PairData,
    test: PairData,
    *,
    include_intercept: bool,
) -> tuple[np.ndarray, dict[str, Any]]:
    retained, selection = _select_features(
        train, validation, include_intercept=include_intercept
    )
    coefficients = _fit_full_coefficients(refit, retained, include_intercept)
    baseline = _fit_full_coefficients(refit, [], include_intercept)
    report = {
        "selection": selection,
        "refit_dates": sorted(set(refit.days.tolist())),
        "test_dates": sorted(set(test.days.tolist())),
        "test_baseline": _metrics(test, baseline, [], include_intercept),
        "test_selected": _metrics(test, coefficients, retained, include_intercept),
        "retained_features": [PAIR_FEATURES[index] for index in retained],
    }
    return coefficients, report


def _rolling_global_rates(
    daily_observed: Sequence[dict[str, Any]], minimum_history_days: int = 2
) -> list[dict[str, Any]]:
    rows = []
    for target_index in range(minimum_history_days, len(daily_observed)):
        history = daily_observed[:target_index]
        target = daily_observed[target_index]
        predicted_retention = sum(row["retained_edges"] for row in history) / max(
            1, sum(row["retention_exposures"] for row in history)
        )
        predicted_formation = sum(row["formation_events"] for row in history) / max(
            1, sum(row["retention_exposures"] for row in history)
        )
        observed_retention = target["global_retention"]
        observed_formation = target["formation_events"] / max(
            1, target["retention_exposures"]
        )
        rows.append(
            {
                "target_date": target["date"],
                "history_days": target_index,
                "predicted_retention": predicted_retention,
                "observed_retention": observed_retention,
                "retention_absolute_error": abs(
                    predicted_retention - observed_retention
                ),
                "predicted_formation_fraction": predicted_formation,
                "observed_formation_fraction": observed_formation,
                "formation_absolute_error": abs(
                    predicted_formation - observed_formation
                ),
            }
        )
    return rows


def _model_id(days: Sequence[DayInput], parameters: dict[str, Any]) -> str:
    inputs = []
    for day in days:
        manifest = read_json(day.layout.manifest_path)
        heartbeat = manifest["raw_files"]["libp2p_synthetic_heartbeat"]
        inputs.append((day.date, heartbeat["sha256"], day.run_dir.name))
    payload = {
        "algorithm": MULTIDAY_ALGORITHM,
        "inputs": inputs,
        "parameters": parameters,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    return f"{MULTIDAY_ALGORITHM}-{digest}"


def _project_day_profiles(
    connection: Any,
    day: DayInput,
    learned_profiles: Sequence[NodeProfile],
) -> list[NodeProfile]:
    metadata = _remote_metadata(connection, day.run_dir)
    _project_effects(
        metadata, {profile.node_id: profile for profile in learned_profiles}
    )
    return list(metadata.values())


def fit_multiday_topology(
    train_days: Sequence[DayInput],
    validation_days: Sequence[DayInput],
    test_days: Sequence[DayInput],
    *,
    output_root: Path,
    node_count: int = 300,
    step_minutes: int = 10,
    mean_degree_min: float = 6.0,
    mean_degree_max: float = 24.0,
    peer_limit: int = 80,
    peer_reserve: int = 16,
    samples_per_day: int = 10_000,
    generation_seeds: Sequence[int] = (1337, 7331, 2027, 4242, 9001),
) -> Path:
    if not train_days or not validation_days or not test_days:
        raise ValueError("train, validation, and test splits must all be non-empty")
    ordered = list(train_days) + list(validation_days) + list(test_days)
    dates = [day.date for day in ordered]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("splits must be unique and strictly chronological")
    safe_peer_limit = peer_limit - peer_reserve
    if node_count < 3:
        raise ValueError("node_count must be at least 3")
    if not 0 < mean_degree_min <= mean_degree_max < node_count:
        raise ValueError("mean-degree range must be positive and below node_count")
    if mean_degree_max >= safe_peer_limit:
        raise ValueError("mean_degree_max must remain below the safe peer limit")
    if samples_per_day < 100:
        raise ValueError("samples_per_day must be at least 100")
    if not generation_seeds:
        raise ValueError("at least one generation seed is required")

    parameters = {
        "node_count": node_count,
        "step_minutes": step_minutes,
        "mean_degree_range": [mean_degree_min, mean_degree_max],
        "peer_limit": peer_limit,
        "peer_reserve": peer_reserve,
        "samples_per_day": samples_per_day,
        "generation_seeds": list(generation_seeds),
        "train_dates": [day.date for day in train_days],
        "validation_dates": [day.date for day in validation_days],
        "test_dates": [day.date for day in test_days],
    }
    model_id = _model_id(ordered, parameters)
    output_dir = output_root / model_id
    if (output_dir / "model.json").exists() and (
        output_dir / "validation_report.json"
    ).exists():
        return output_dir
    output_root.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{model_id}.tmp-", dir=output_root)
    )
    duckdb = require_duckdb()
    connection = duckdb.connect()
    try:
        connection.execute("SET TimeZone='UTC'")
        connection.execute("SET preserve_insertion_order=false")
        train_observers, train_observer_report = _stable_observers(
            connection, train_days
        )
        train_profiles, train_observed, train_daily = _aggregate_profiles(
            connection,
            train_days,
            step_minutes,
            observer_ids=train_observers,
        )
        train_formation = _collect_pair_data(
            connection,
            train_days,
            train_profiles,
            step_minutes,
            samples_per_day,
            1337,
            "formation",
            train_observers,
        )
        train_retention = _collect_pair_data(
            connection,
            train_days,
            train_profiles,
            step_minutes,
            samples_per_day,
            2337,
            "retention",
            train_observers,
        )
        validation_formation = _collect_pair_data(
            connection,
            validation_days,
            train_profiles,
            step_minutes,
            samples_per_day,
            3337,
            "formation",
            train_observers,
        )
        validation_retention = _collect_pair_data(
            connection,
            validation_days,
            train_profiles,
            step_minutes,
            samples_per_day,
            4337,
            "retention",
            train_observers,
        )

        refit_days = list(train_days) + list(validation_days)
        refit_observers, refit_observer_report = _stable_observers(
            connection, refit_days
        )
        refit_profiles, refit_observed, validation_daily = _aggregate_profiles(
            connection,
            refit_days,
            step_minutes,
            observer_ids=refit_observers,
        )
        refit_formation = _collect_pair_data(
            connection,
            refit_days,
            refit_profiles,
            step_minutes,
            samples_per_day,
            5337,
            "formation",
            refit_observers,
        )
        refit_retention = _collect_pair_data(
            connection,
            refit_days,
            refit_profiles,
            step_minutes,
            samples_per_day,
            6337,
            "retention",
            refit_observers,
        )
        test_formation = _collect_pair_data(
            connection,
            test_days,
            refit_profiles,
            step_minutes,
            samples_per_day,
            7337,
            "formation",
            refit_observers,
        )
        test_retention = _collect_pair_data(
            connection,
            test_days,
            refit_profiles,
            step_minutes,
            samples_per_day,
            8337,
            "retention",
            refit_observers,
        )
        formation_coefficients, formation_fit = _fit_and_test(
            train_formation,
            validation_formation,
            refit_formation,
            test_formation,
            include_intercept=False,
        )
        retention_coefficients, retention_fit = _fit_and_test(
            train_retention,
            validation_retention,
            refit_retention,
            test_retention,
            include_intercept=True,
        )

        test_daily: list[dict[str, Any]] = []
        for day in test_days:
            _, observed, _ = _aggregate_profiles(
                connection,
                [day],
                step_minutes,
                fit_node_effects=False,
                observer_ids=refit_observers,
            )
            formation_day = PairData(
                test_formation.features[test_formation.days == day.date],
                test_formation.outcomes[test_formation.days == day.date],
                test_formation.offsets[test_formation.days == day.date],
                test_formation.days[test_formation.days == day.date],
                test_formation.known_fraction,
            )
            retention_day = PairData(
                test_retention.features[test_retention.days == day.date],
                test_retention.outcomes[test_retention.days == day.date],
                test_retention.offsets[test_retention.days == day.date],
                test_retention.days[test_retention.days == day.date],
                test_retention.known_fraction,
            )
            formation_retained = [
                PAIR_FEATURES.index(name)
                for name in formation_fit["retained_features"]
            ]
            retention_retained = [
                PAIR_FEATURES.index(name)
                for name in retention_fit["retained_features"]
            ]
            test_daily.append(
                {
                    "date": day.date,
                    "observed": observed,
                    "formation_pair": _metrics(
                        formation_day,
                        formation_coefficients,
                        formation_retained,
                        False,
                    ),
                    "retention_pair": _metrics(
                        retention_day,
                        retention_coefficients,
                        retention_retained,
                        True,
                    ),
                }
            )

        all_daily = train_daily + validation_daily[len(train_days):] + [
            row for day in test_daily for row in [{"date": day["date"], **day["observed"]}]
        ]
        rolling = _rolling_global_rates(all_daily)
        last_test = test_days[-1]
        projected = _project_day_profiles(connection, last_test, refit_profiles)
        selected, initial_bucket, initial_candidates = _select_nodes(
            connection,
            last_test.run_dir,
            projected,
            node_count,
            generation_seeds[0],
            refit_observers,
        )
        pairs, formation_scores, retention_scores, _ = _pair_scores(
            selected, formation_coefficients, retention_coefficients
        )
        seed_reports = []
        observed_formation_ratio = refit_observed[
            "global_formation_fraction_per_active_edge"
        ]
        for seed in generation_seeds:
            initial, initial_probabilities, initial_report = _generate_initial_graph(
                node_count,
                pairs,
                formation_scores + retention_scores,
                mean_degree_min,
                mean_degree_max,
                safe_peer_limit,
                seed,
            )
            snapshots, transitions, churn = _evolve_graphs(
                node_count,
                initial,
                pairs,
                formation_scores,
                retention_scores,
                refit_observed["global_retention"],
                observed_formation_ratio,
                safe_peer_limit,
                5,
                seed,
            )
            seed_dir = temporary_dir / f"seed-{seed}"
            seed_dir.mkdir()
            _write_outputs(
                connection,
                seed_dir,
                model_id,
                selected,
                snapshots,
                transitions,
                step_minutes,
                initial_probabilities,
            )
            snapshot_stats = []
            structural_valid = True
            for snapshot_index, edges in enumerate(snapshots):
                degrees = _degrees(node_count, edges)
                components = len(_components(node_count, edges))
                stats = {
                    "snapshot_index": snapshot_index,
                    "edges": len(edges),
                    "components": components,
                    "isolated_nodes": sum(value == 0 for value in degrees),
                    "mean_degree": 2.0 * len(edges) / node_count,
                    "maximum_degree": max(degrees),
                    "degree_quantiles": _quantiles([float(value) for value in degrees]),
                }
                structural_valid &= (
                    components == 1
                    and stats["isolated_nodes"] == 0
                    and stats["maximum_degree"] <= safe_peer_limit
                    and mean_degree_min <= stats["mean_degree"] <= mean_degree_max
                )
                snapshot_stats.append(stats)
            seed_reports.append(
                {
                    "seed": seed,
                    "valid": bool(structural_valid),
                    "initial_generation": initial_report,
                    "churn": churn,
                    "snapshots": snapshot_stats,
                    "mean_generated_retention": float(
                        np.mean([row["retention_fraction"] for row in transitions])
                    ),
                    "mean_generated_formation_fraction": float(
                        np.mean([row["formation_fraction"] for row in transitions])
                    ),
                }
            )
    finally:
        connection.close()

    observed_test_retention = sum(
        day["observed"]["retained_edges"] for day in test_daily
    ) / max(
        1, sum(day["observed"]["retention_exposures"] for day in test_daily)
    )
    observed_test_formation = sum(
        day["observed"]["formation_events"] for day in test_daily
    ) / max(
        1, sum(day["observed"]["retention_exposures"] for day in test_daily)
    )
    generated_retention = [row["mean_generated_retention"] for row in seed_reports]
    generated_formation = [
        row["mean_generated_formation_fraction"] for row in seed_reports
    ]
    retention_generation_error = abs(
        float(np.mean(generated_retention)) - observed_test_retention
    )
    formation_generation_error = abs(
        float(np.mean(generated_formation)) - observed_test_formation
    )
    pair_generalization_tolerance = 0.005
    validation_criteria = {
        "all_seeded_graphs_structurally_valid": all(
            row["valid"] for row in seed_reports
        ),
        "formation_test_endpoint_coverage_at_least_90_percent": (
            test_formation.known_fraction >= 0.90
        ),
        "retention_test_endpoint_coverage_at_least_90_percent": (
            test_retention.known_fraction >= 0.90
        ),
        "formation_generation_error_at_most_0_08": (
            formation_generation_error <= 0.08
        ),
        "retention_generation_error_at_most_0_08": (
            retention_generation_error <= 0.08
        ),
        "formation_pair_log_loss_not_degraded_over_0_005": (
            formation_fit["test_selected"]["log_loss"]
            <= formation_fit["test_baseline"]["log_loss"]
            + pair_generalization_tolerance
        ),
        "retention_pair_log_loss_not_degraded_over_0_005": (
            retention_fit["test_selected"]["log_loss"]
            <= retention_fit["test_baseline"]["log_loss"]
            + pair_generalization_tolerance
        ),
    }
    validation = {
        "schema_version": 1,
        "model_id": model_id,
        "generated_at": utc_now(),
        "valid": all(validation_criteria.values()),
        "validation_criteria": validation_criteria,
        "temporal_split": {
            "train": parameters["train_dates"],
            "validation": parameters["validation_dates"],
            "test": parameters["test_dates"],
            "test_used_for_selection_or_fitting": False,
        },
        "pair_model": {
            "formation": formation_fit,
            "retention": retention_fit,
        },
        "observer_identity_freeze": {
            "selection_fit": train_observer_report,
            "final_refit": refit_observer_report,
            "test_observers_used_for_cohort_selection": False,
        },
        "daily_test_metrics": test_daily,
        "rolling_global_rate_evaluation": rolling,
        "generation_across_seeds": {
            "reports": seed_reports,
            "test_observed_retention": observed_test_retention,
            "generated_retention_mean": float(np.mean(generated_retention)),
            "generated_retention_std": float(np.std(generated_retention)),
            "retention_absolute_error": retention_generation_error,
            "test_observed_formation_fraction": observed_test_formation,
            "generated_formation_mean": float(np.mean(generated_formation)),
            "generated_formation_std": float(np.std(generated_formation)),
            "formation_absolute_error": formation_generation_error,
        },
        "limitations": [
            "Xatu does not expose remote-to-remote ground-truth adjacency.",
            "Formation opportunity is an observer-availability approximation.",
            "Unseen test peers receive the global zero node-effect prior.",
            "Graph density remains an external broad range rather than a learned degree target.",
        ],
    }
    atomic_write_json(temporary_dir / "validation_report.json", validation)
    model = {
        "schema_version": 1,
        "model_id": model_id,
        "algorithm": MULTIDAY_ALGORITHM,
        "generated_at": utc_now(),
        "parameters": parameters,
        "source_inputs": [
            {
                "date": day.date,
                "dataset_id": day.layout.dataset_id,
                "topology_input_run": day.run_dir.name,
            }
            for day in ordered
        ],
        "train_observed": train_observed,
        "refit_observed": refit_observed,
        "observer_identity_freeze": {
            "selection_fit": train_observer_report,
            "final_refit": refit_observer_report,
        },
        "formation_coefficients": {
            name: float(value)
            for name, value in zip(PAIR_FEATURES, formation_coefficients)
        },
        "retention_coefficients": {
            "intercept": float(retention_coefficients[0]),
            **{
                name: float(value)
                for name, value in zip(PAIR_FEATURES, retention_coefficients[1:])
            },
        },
        "initial_test_date": last_test.date,
        "initial_test_bucket": initial_bucket,
        "initial_candidate_nodes": len(initial_candidates),
        "selected_nodes": len(selected),
        "validation_report": "validation_report.json",
    }
    atomic_write_json(temporary_dir / "model.json", model)
    _write_csv(
        temporary_dir / "rolling_global_rates.csv",
        (
            "target_date",
            "history_days",
            "predicted_retention",
            "observed_retention",
            "retention_absolute_error",
            "predicted_formation_fraction",
            "observed_formation_fraction",
            "formation_absolute_error",
        ),
        ([row[key] for key in (
            "target_date",
            "history_days",
            "predicted_retention",
            "observed_retention",
            "retention_absolute_error",
            "predicted_formation_fraction",
            "observed_formation_fraction",
            "formation_absolute_error",
        )] for row in rolling),
    )
    _write_csv(
        temporary_dir / "daily_test_metrics.csv",
        (
            "date",
            "observed_retention",
            "observed_formation_fraction",
            "formation_log_loss",
            "formation_accuracy",
            "retention_log_loss",
            "retention_brier_score",
            "retention_accuracy",
            "retention_auc",
        ),
        (
            (
                row["date"],
                row["observed"]["global_retention"],
                row["observed"]["global_formation_fraction_per_active_edge"],
                row["formation_pair"]["log_loss"],
                row["formation_pair"]["accuracy"],
                row["retention_pair"]["log_loss"],
                row["retention_pair"]["brier_score"],
                row["retention_pair"]["accuracy"],
                row["retention_pair"]["auc"],
            )
            for row in test_daily
        ),
    )
    _write_csv(
        temporary_dir / "generation_seed_metrics.csv",
        (
            "seed",
            "valid",
            "mean_generated_retention",
            "mean_generated_formation_fraction",
            "minimum_edges",
            "maximum_edges",
            "minimum_mean_degree",
            "maximum_mean_degree",
            "maximum_degree",
        ),
        (
            (
                row["seed"],
                row["valid"],
                row["mean_generated_retention"],
                row["mean_generated_formation_fraction"],
                min(snapshot["edges"] for snapshot in row["snapshots"]),
                max(snapshot["edges"] for snapshot in row["snapshots"]),
                min(snapshot["mean_degree"] for snapshot in row["snapshots"]),
                max(snapshot["mean_degree"] for snapshot in row["snapshots"]),
                max(snapshot["maximum_degree"] for snapshot in row["snapshots"]),
            )
            for row in seed_reports
        ),
    )
    if output_dir.exists():
        raise RuntimeError(f"incomplete multiday model already exists: {output_dir}")
    temporary_dir.replace(output_dir)
    return output_dir
