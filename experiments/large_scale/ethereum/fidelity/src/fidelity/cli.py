from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from .config import PipelineConfig
from .etherbee import archive_public_catalog
from .modeling import fit_model
from .multiday import fit_multiday_topology, resolve_days
from .normalize import normalize_dataset
from .reconstruct import latest_run, reconstruct_dataset
from .report import generate_report
from .storage import DatasetLayout
from .temporal import audit_snapshots, build_snapshots
from .topology import fit_topology
from .topology_prep import prepare_topology_day
from .validate import validate_dataset
from .xatu import discover_latest_complete_day, fetch_dataset


FIDELITY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = FIDELITY_ROOT / "configs" / "xatu-latest-day.yaml"
DEFAULT_DATASETS = FIDELITY_ROOT / "datasets"


def _config(args: argparse.Namespace) -> PipelineConfig:
    config = PipelineConfig.from_file(args.config)
    if getattr(args, "date", None):
        config = replace(config, date=args.date)
    if getattr(args, "network", None):
        config = replace(config, network=args.network)
    config.validate()
    return config


def _layout(path: str) -> DatasetLayout:
    layout = DatasetLayout.from_dataset_path(Path(path))
    if not layout.manifest_path.exists():
        raise RuntimeError(f"dataset manifest does not exist: {layout.manifest_path}")
    return layout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconstruct measured Ethereum Beacon P2P ego-network graphs"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--datasets-root", type=Path, default=DEFAULT_DATASETS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="find latest complete Xatu date")
    discover.add_argument("--network")

    fetch = subparsers.add_parser("fetch", help="download immutable raw Xatu parquet")
    fetch.add_argument("--date", help="YYYY-MM-DD or latest from config")
    fetch.add_argument("--network")

    normalize = subparsers.add_parser("normalize", help="normalize raw Xatu tables")
    normalize.add_argument("--dataset", required=True)

    reconstruct = subparsers.add_parser("reconstruct", help="derive G0, C, C_new and intervals")
    reconstruct.add_argument("--dataset", required=True)
    reconstruct.add_argument("--bucket-seconds", type=int)
    reconstruct.add_argument("--no-csv", action="store_true")

    report = subparsers.add_parser("report", help="create coverage report, plots and GraphML")
    report.add_argument("--dataset", required=True)
    report.add_argument("--run-dir")

    validate = subparsers.add_parser("validate", help="verify provenance and graph semantics")
    validate.add_argument("--dataset", required=True)
    validate.add_argument("--run-dir")
    validate.add_argument("--skip-raw-checksums", action="store_true")

    snapshots = subparsers.add_parser(
        "snapshots", help="derive all heartbeat snapshots and heartbeat sessions"
    )
    snapshots.add_argument("--dataset", required=True)
    snapshots.add_argument("--run-dir")
    snapshots.add_argument("--bucket-seconds", type=int)
    snapshots.add_argument("--session-gap-seconds", type=int, default=180)

    audit = subparsers.add_parser(
        "audit", help="profile and classify Xatu observer behavior"
    )
    audit.add_argument("--dataset", required=True)
    audit.add_argument("--run-dir")

    model = subparsers.add_parser(
        "fit-model", help="fit a bounded-observer topology and churn model"
    )
    model.add_argument("--dataset", required=True)
    model.add_argument("--run-dir")
    model.add_argument("--node-count", type=int)
    model.add_argument("--seed", type=int, default=1337)
    model.add_argument("--swaps-per-edge", type=float, default=2.0)

    topology = subparsers.add_parser(
        "fit-topology",
        help="fit the approved remote-peer propensity model and generate six graphs",
    )
    topology.add_argument("--dataset", required=True)
    topology.add_argument("--run-dir")
    topology.add_argument("--node-count", type=int, default=300)
    topology.add_argument("--step-minutes", type=int, default=10)
    topology.add_argument("--mean-degree-min", type=float, default=6.0)
    topology.add_argument("--mean-degree-max", type=float, default=24.0)
    topology.add_argument("--peer-limit", type=int, default=80)
    topology.add_argument("--peer-reserve", type=int, default=16)
    topology.add_argument("--sample-limit", type=int, default=50_000)
    topology.add_argument("--seed", type=int, default=1337)

    prepare_topology = subparsers.add_parser(
        "prepare-topology-day",
        help="derive compact topology inputs directly from one raw heartbeat day",
    )
    prepare_topology.add_argument("--dataset", required=True)
    prepare_topology.add_argument("--bucket-seconds", type=int, default=60)
    prepare_topology.add_argument("--session-gap-seconds", type=int, default=180)

    multiday = subparsers.add_parser(
        "fit-topology-multiday",
        help="fit with chronological train/validation/test days and no test leakage",
    )
    multiday.add_argument("--train-datasets", nargs="+", required=True)
    multiday.add_argument("--validation-datasets", nargs="+", required=True)
    multiday.add_argument("--test-datasets", nargs="+", required=True)
    multiday.add_argument(
        "--output-root", type=Path, default=FIDELITY_ROOT / "multiday_models"
    )
    multiday.add_argument("--node-count", type=int, default=300)
    multiday.add_argument("--step-minutes", type=int, default=10)
    multiday.add_argument("--mean-degree-min", type=float, default=6.0)
    multiday.add_argument("--mean-degree-max", type=float, default=24.0)
    multiday.add_argument("--peer-limit", type=int, default=80)
    multiday.add_argument("--peer-reserve", type=int, default=16)
    multiday.add_argument("--samples-per-day", type=int, default=10_000)
    multiday.add_argument(
        "--generation-seeds",
        nargs="+",
        type=int,
        default=[1337, 7331, 2027, 4242, 9001],
    )

    all_command = subparsers.add_parser("all", help="run fetch through validation")
    all_command.add_argument("--date")
    all_command.add_argument("--network")
    all_command.add_argument("--no-csv", action="store_true")

    etherbee = subparsers.add_parser(
        "etherbee-catalog", help="archive EtherBee public OSF metadata only"
    )
    etherbee.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = _config(args)
    datasets_root = args.datasets_root.resolve()

    if args.command == "discover":
        day = discover_latest_complete_day(
            config.network, config.required_tables, config.lookback_days
        )
        print(json.dumps({"network": config.network, "latest_complete_date": day.isoformat()}))
        return 0
    if args.command == "fetch":
        layout = fetch_dataset(config, datasets_root)
        print(layout.path)
        return 0
    if args.command == "normalize":
        print(normalize_dataset(_layout(args.dataset)))
        return 0
    if args.command == "reconstruct":
        layout = _layout(args.dataset)
        bucket = args.bucket_seconds or config.snapshot_bucket_seconds
        print(reconstruct_dataset(layout, bucket, export_csv=not args.no_csv))
        return 0
    if args.command == "report":
        layout = _layout(args.dataset)
        run = Path(args.run_dir).resolve() if args.run_dir else latest_run(layout)
        print(generate_report(layout, run))
        return 0
    if args.command == "validate":
        layout = _layout(args.dataset)
        run = Path(args.run_dir).resolve() if args.run_dir else latest_run(layout)
        print(validate_dataset(layout, run, not args.skip_raw_checksums))
        return 0
    if args.command == "snapshots":
        layout = _layout(args.dataset)
        run = Path(args.run_dir).resolve() if args.run_dir else latest_run(layout)
        bucket = args.bucket_seconds or config.snapshot_bucket_seconds
        print(build_snapshots(layout, run, bucket, args.session_gap_seconds))
        return 0
    if args.command == "audit":
        layout = _layout(args.dataset)
        run = Path(args.run_dir).resolve() if args.run_dir else latest_run(layout)
        print(audit_snapshots(layout, run))
        return 0
    if args.command == "fit-model":
        layout = _layout(args.dataset)
        run = Path(args.run_dir).resolve() if args.run_dir else latest_run(layout)
        print(
            fit_model(
                layout,
                run,
                node_count=args.node_count,
                seed=args.seed,
                swaps_per_edge=args.swaps_per_edge,
            )
        )
        return 0
    if args.command == "fit-topology":
        layout = _layout(args.dataset)
        run = Path(args.run_dir).resolve() if args.run_dir else latest_run(layout)
        print(
            fit_topology(
                layout,
                run,
                node_count=args.node_count,
                snapshot_count=5,
                step_minutes=args.step_minutes,
                mean_degree_min=args.mean_degree_min,
                mean_degree_max=args.mean_degree_max,
                peer_limit=args.peer_limit,
                peer_reserve=args.peer_reserve,
                sample_limit=args.sample_limit,
                seed=args.seed,
            )
        )
        return 0
    if args.command == "prepare-topology-day":
        print(
            prepare_topology_day(
                _layout(args.dataset),
                bucket_seconds=args.bucket_seconds,
                session_gap_seconds=args.session_gap_seconds,
            )
        )
        return 0
    if args.command == "fit-topology-multiday":
        train = resolve_days([_layout(path) for path in args.train_datasets])
        validation = resolve_days(
            [_layout(path) for path in args.validation_datasets]
        )
        test = resolve_days([_layout(path) for path in args.test_datasets])
        print(
            fit_multiday_topology(
                train,
                validation,
                test,
                output_root=args.output_root.resolve(),
                node_count=args.node_count,
                step_minutes=args.step_minutes,
                mean_degree_min=args.mean_degree_min,
                mean_degree_max=args.mean_degree_max,
                peer_limit=args.peer_limit,
                peer_reserve=args.peer_reserve,
                samples_per_day=args.samples_per_day,
                generation_seeds=args.generation_seeds,
            )
        )
        return 0
    if args.command == "all":
        layout = fetch_dataset(config, datasets_root)
        normalize_dataset(layout)
        run = reconstruct_dataset(
            layout,
            config.snapshot_bucket_seconds,
            export_csv=not args.no_csv,
        )
        generate_report(layout, run)
        print(validate_dataset(layout, run))
        return 0
    if args.command == "etherbee-catalog":
        output = args.output or datasets_root / "etherbee-public-catalog.json"
        print(archive_public_catalog(output.resolve()))
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
