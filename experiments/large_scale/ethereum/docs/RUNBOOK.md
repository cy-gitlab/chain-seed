# Experiment Runbook

Run commands from:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum
```

## Build Lighthouse Fork Image

Run once before any `fork_lite` or `baseline_all_fork_lite` variant:

```bash
LIGHTHOUSE_FORK_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-seedemu-lite \
scripts/build_lighthouse_seedemu_fork.sh
```

## Reproduce 10-Beacon Comparison

This runs 10 beacon, 10 geth, and 30 validator nodes for 30 minutes per variant
with transaction load enabled.

```bash
RUN_PREFIX="$(date +%Y%m%d-%H%M%S)-b10-single-var" \
scripts/run_10_beacon_single_variable_compare.sh
```

Default variants:

```text
baseline_all no_historic low_cache low_peers fork_lite
```

## Reproduce 300-Beacon Comparison

This runs 300 beacon, 300 geth, and 900 validator nodes for 10 hours per variant
with transaction load enabled.

```bash
RUN_PREFIX="$(date +%Y%m%d-%H%M%S)-b300-single-var-10h" \
scripts/run_300_beacon_single_variable_compare.sh
```

Default variants:

```text
baseline_all no_historic low_cache_only low_peers_only baseline_all_fork_lite
```

## Re-run Only the Lighthouse Fork 300-Beacon Variant

Use this when only refreshing the fork result:

```bash
RUN_PREFIX="$(date +%Y%m%d-%H%M%S)-b300-beacon-fork-lite-only-10h" \
VARIANTS="baseline_all_fork_lite" \
BEACONS=300 \
STUB_AS_COUNT=64 \
DURATION_SECONDS=36000 \
MEMORY_INTERVAL=60 \
TX_RATE=1 \
BUILD_MODE=nobuild-mount \
UP_PRESET=fast \
KEEP_LAST=1 \
scripts/run_beacon_memory_optimization_compare.sh
```

## Verify Transaction Load

After the tx sender starts:

```bash
tail -f experiments/<run-id>/<variant>/tx-load/stdout.log
```

The log should show increasing `sent=` counts. The block metrics should also
show non-empty blocks:

```bash
tail -n 20 experiments/<run-id>/<variant>/block_metrics.csv
```

The `tx_count` column must be greater than zero for transaction-loaded runs.

## Check Results

Each run writes:

```text
experiments/<run-id>/summary.csv
experiments/<run-id>/summary.md
```

For curated historical data:

```text
experiments/10-beacon-single-variable/summary.csv
experiments/300-beacon-single-variable/summary.csv
```

## Stop a Running Experiment

If `KEEP_LAST=1` was used, stop the current generated stack with:

```bash
docker compose -f output/docker-compose.yml down -v --remove-orphans
```

Do this before deleting or regenerating `output/`.

## API Worker Contention

The controlled API concurrency experiment has a separate runbook:

```text
docs/RUNBOOK_API_WORKER_CONTENTION.md
```
