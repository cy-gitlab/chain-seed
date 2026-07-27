# Static Beacon P2P Topology Runbook

## Scope

This workflow downloads and processes public static data and can fit a
synthetic remote-to-remote topology model. It does not start Docker or alter
the current D61 Ethereum experiment.

The observed graph remains the union of Xatu ego-network observations.
Generated remote-to-remote edges are stored separately and are always marked
synthetic; they are not claimed as recovered Mainnet adjacency.

All normalized timestamps are stored as timezone-naive UTC values because Xatu
event timestamps are defined in UTC.

## Environment

```bash
cd ~/chain-seed/experiments/large_scale/ethereum/fidelity
./scripts/setup_env.sh
```

The scripts use the existing `fidelity` Conda environment by default. Both
dependency installation and all subsequent commands run inside that
environment. Override it with `CONDA_ENV_NAME=<name>` when necessary.

To bypass Conda and use another Python environment explicitly:

```bash
python -m pip install -r requirements.txt
PYTHON_BIN=python ./scripts/fidelity_data.sh --help
```

## Discover available data

Xatu collects continuously, but public Parquet data is published in daily
partitions and normally lags collection. Find the newest date for which all
three topology-required tables are available:

```bash
./scripts/fidelity_data.sh discover
```

Always record the returned date. The dataset manifest pins it after download.

## Download immutable raw files

For a reproducible run, pass the discovered date explicitly:

```bash
./scripts/fidelity_data.sh fetch --date 2026-07-16
```

This downloads complete Parquet files for heartbeat, connected, and disconnected
events. The identify table is also archived when the same date is public; its
absence is recorded in the manifest and does not block reconstruction.
Interrupted downloads resume from `.part` files. Existing raw files are reused
only when their SHA-256 matches the manifest. The command prints a dataset path
such as:

```text
.../fidelity/datasets/xatu-mainnet-2026-07-16
```

## Normalize and reconstruct

```bash
DATASET="$PWD/datasets/xatu-mainnet-2026-07-16"

./scripts/fidelity_data.sh normalize --dataset "$DATASET"
./scripts/fidelity_data.sh reconstruct --dataset "$DATASET"
./scripts/fidelity_data.sh report --dataset "$DATASET"
./scripts/fidelity_data.sh validate --dataset "$DATASET"
```

`reconstruct` writes both Parquet and CSV for derived tables. Use `--no-csv` if
disk space is limited; this never removes raw or normalized Parquet. The
equivalent one-command workflow is:

```bash
./scripts/fidelity_data.sh all --date 2026-07-16
```

Do not run `all` until enough disk space is available for raw, normalized,
derived, CSV, GraphML, and temporary DuckDB data.

## Outputs

```text
datasets/xatu-mainnet-YYYY-MM-DD/
├── raw/                         original immutable Xatu Parquet
├── normalized/                  common observation schema and provenance
├── derived/<run-id>/            G0, C, C_new, events and intervals
├── reports/<run-id>/            validation, JSON, plots and GraphML
└── manifest.json                source URLs, hashes and processing runs
```

Key graph files:

- `g0_edges.parquet`: initial heartbeat snapshot.
- `candidate_edges.parquet`: complete observed candidate set `C`, including G0.
- `replacement_edges.parquet`: `C_new = C - G0`.
- `edge_history.parquet`: first/last observation and event counts for every pair.
- `connection_intervals.parquet`: complete and censored connection intervals.

`validation_report.json` must contain `"valid": true`. The coverage report
explicitly states that this is not a complete mainnet ground-truth adjacency
graph.

## Temporal snapshots

Generate every one-minute heartbeat snapshot and infer heartbeat sessions. A
session is split when the reported connection age resets. The three-minute gap
threshold is used when connection-age evidence is unavailable; a long heartbeat
gap does not split a connection whose reported age continues consistently:

```bash
./scripts/fidelity_data.sh snapshots --dataset "$DATASET" \
    --bucket-seconds 60 \
    --session-gap-seconds 180
```

The command writes under the selected reconstruction run:

```text
derived/<run-id>/snapshots/
├── snapshot_edges.parquet
├── snapshot_summary.parquet
├── observer_timeseries.parquet
├── heartbeat_cadence.parquet
├── heartbeat_sessions.parquet
└── snapshot_manifest.json
```

`snapshot_edges.parquet` is intentionally large because it preserves every
deduplicated observer-to-peer heartbeat bucket. No observer or edge is removed.

## Observer audit

```bash
./scripts/fidelity_data.sh audit --dataset "$DATASET"
```

The audit classifies observers using their temporal degree distribution:

- `bounded`: retained for the legacy one-day observer-behavior analysis; it
  must not be interpreted as a remote-peer degree sample.
- `bursty-probe`: usually bounded but occasionally exceeds the robust degree
  fence.
- `bulk-probe`: sustained or extreme high-degree measurement behavior.

The robust fence and exact classification rules are recorded in
`data_quality_report.json`. This is a model-selection label, not deletion: all
cohorts remain in `observer_profiles.parquet`, `observer_cohorts.parquet`, and
the snapshot files.

Review these fields before fitting:

```bash
python -m json.tool \
    "$DATASET/derived/observed-ego-v1-8fec48011881/snapshots/data_quality_report.json"
```

The run ID can differ for another source date or reconstruction configuration.
Omit the literal path and inspect `derived/` when necessary.

## Legacy one-day model

The following workflow is retained only to reproduce the earlier one-day
prototype. It is not an approved topology result because it fits remote-node
degree from observer behavior, which Xatu cannot identify.

Fit the default full-size model using every observed remote peer and the
bounded observer cohort:

```bash
./scripts/fidelity_data.sh fit-model --dataset "$DATASET" \
    --seed 1337 \
    --swaps-per-edge 2
```

For a small implementation check without changing the source dataset:

```bash
./scripts/fidelity_data.sh fit-model --dataset "$DATASET" \
    --node-count 300 \
    --seed 1337
```

Model outputs are immutable for the same source, parameters, and seed:

```text
models/<model-id>/
├── model.json
├── churn_model.json
├── churn_samples.parquet
├── full_size_nodes.parquet
├── full_size_snapshot.parquet
└── validation_report.json
```

`full_size_snapshot.parquet` contains generated remote-to-remote edges. Every
row has `synthetic=true`. The generator matches the bounded-observer active
degree distribution, randomizes a simple graph with degree-preserving edge
swaps, and guarantees connectivity. Client, ASN, country, outbound ratio,
latency, and remote visibility distributions are recorded as calibration
parameters. Client-to-client or ASN-to-ASN edge mixing is deliberately not
inferred because it is not identifiable from the public ego graph.

The legacy one-day model reports
`scientific_validation_status=provisional-single-day`. It is suitable for
pipeline regression checks, not for a paper result.

## Approved propensity topology model

After `snapshots` and `audit` have completed, fit the agreed model and generate
an initial 300-node graph plus five ten-minute churn snapshots:

```bash
./scripts/fidelity_data.sh fit-topology --dataset "$DATASET" \
    --node-count 300 \
    --step-minutes 10 \
    --mean-degree-min 6 \
    --mean-degree-max 24 \
    --peer-limit 80 \
    --peer-reserve 16 \
    --sample-limit 50000 \
    --seed 1337
```

The degree range is a broad graph-level sanity interval, not an exact edge
count and not a per-node degree target. `peer-limit - peer-reserve` is the only
node-level upper safety bound. Every active graph must remain connected and
contain no isolated node.

The model uses bounded observers only as measurement vantage points. It learns:

- node formation propensity from new observed edges per availability exposure;
- node persistence from ten-minute edge retention and censored session data;
- ASN, organization, country, and city pair effects from time-split ranking and
  retention tasks;
- Xatu's global formation and retention rates for churn calibration.

A pair feature is used only when its coefficient has the same sign in both
training halves and improves held-out log loss. Observer degree is never copied
to remote nodes.

Outputs are written atomically under:

```text
datasets/xatu-mainnet-YYYY-MM-DD/topology_models/<model-id>/
├── model.json
├── validation_report.json
├── nodes.csv
├── nodes.parquet
├── snapshots.csv
├── snapshots.parquet
├── edge_events.csv
├── edge_events.parquet
└── snapshot_0.csv ... snapshot_5.csv
```

`snapshot_0` is the initial graph and `snapshot_1` through `snapshot_5` are the
five requested changes. The command fails rather than publishing the model if
any graph is disconnected, has an isolated node, exceeds the safe peer limit,
falls outside the broad density range, or misses Xatu formation/retention rates
by more than eight percentage points. Always confirm:

```bash
python -m json.tool \
    "$DATASET/topology_models/<model-id>/validation_report.json"
```

The current result is a one-day, within-day held-out model. It supports an
initial emulator experiment, not a claim that Mainnet's hidden edges were
recovered. Multi-day held-out validation remains required before reporting it
as a paper result.

## Multi-day topology model

The paper-oriented workflow uses ten consecutive immutable Xatu partitions.
It avoids the large normalized observation copies by deriving only topology
inputs from each raw heartbeat file:

```bash
for date in {07..16}; do
    ./scripts/fidelity_data.sh prepare-topology-day \
        --dataset "$PWD/datasets/xatu-mainnet-2026-07-${date}"
done
```

Each prepared day contains minute-level ego edges, censored heartbeat sessions,
observer cohorts, and node attributes under a deterministic
`derived/xatu-topology-input-v1-*` run. Raw Parquet files are never modified.

Fit the chronological split with five training days, two validation days, and
three final test days:

```bash
./scripts/fidelity_data.sh fit-topology-multiday \
    --train-datasets \
        datasets/xatu-mainnet-2026-07-{07..11} \
    --validation-datasets \
        datasets/xatu-mainnet-2026-07-{12..13} \
    --test-datasets \
        datasets/xatu-mainnet-2026-07-{14..16} \
    --node-count 300 \
    --step-minutes 10 \
    --mean-degree-min 6 \
    --mean-degree-max 24 \
    --peer-limit 80 \
    --peer-reserve 16 \
    --samples-per-day 10000 \
    --generation-seeds 1337 7331 2027 4242 9001
```

The split has the following semantics:

- Training days fit node effects, Ridge coefficients, candidate pair models,
  and the stable bounded-observer identity set.
- Validation days select pair features and are then included in a final refit.
- Test days are used only after all parameters and features are frozen.
- A peer absent from the fitted days receives the global zero-effect prior;
  the report records endpoint coverage so this fallback remains visible.

Results are written atomically under `multiday_models/<model-id>/`. Review
`validation_report.json` for independent test log loss, Brier score, AUC,
per-day results, expanding-window global-rate errors, and the variance across
five graph-generation seeds. Every `seed-*/` directory contains the generated
initial graph and five churn snapshots. `daily_test_metrics.csv`,
`rolling_global_rates.csv`, and `generation_seed_metrics.csv` are flat tables
for plotting. The top-level `valid` flag requires structural validity for all
seeds, at least 90% fitted-peer endpoint coverage, formation and retention
generation errors no larger than 0.08, and no pair-model test log-loss
degradation larger than 0.005.

## Complete legacy current-day workflow

For a dataset that has already passed the original reconstruction validation:

```bash
./scripts/fidelity_data.sh snapshots --dataset "$DATASET"
./scripts/fidelity_data.sh audit --dataset "$DATASET"
./scripts/fidelity_data.sh fit-model --dataset "$DATASET"
```

## EtherBee public metadata

Archive the public OSF catalog separately:

```bash
./scripts/fidelity_data.sh etherbee-catalog
```

EtherBee is auxiliary in this stage. Its records do not create G0 or C edges.
