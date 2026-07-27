# Ethereum Lighthouse Fork Component Benchmark Runbook

This runbook is for the 300-beacon, 10-hour, fork component benchmark. It is
designed to collect raw data for the paper benchmark tables. It does not delete
previous experiment data.

## Goal

Run Lighthouse fork optimizations as single-variable experiments:

- `baseline`: official Lighthouse image, no fork component enabled.
- `fork_cache_only`: only beacon cache limits enabled.
- `fork_light_client_only`: only light client server disabled.
- `fork_processor_only`: only beacon processor worker, queue, and gossip batch limits enabled.
- `fork_rayon_only`: only Rayon thread-pool cap enabled.
- `fork_quic_only`: only QUIC support disabled.
- `fork_all`: all fork components enabled.

All variants use the same Ethereum scale and topology:

- `300` beacon nodes.
- `300` geth nodes.
- `900` validator clients.
- Beacon historic state reconstruction remains enabled for all beacons.
- Lighthouse CLI cache profile remains default.
- Beacon peer settings remain default.
- Transaction workload remains enabled.
- Each variant runs for `10` hours after health check passes.

This keeps the experiment single-variable relative to the fork component being
tested.

## Build the Component-Aware Fork Image

Run from `experiments/large_scale/ethereum`:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

LIGHTHOUSE_BASE_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0 \
LIGHTHOUSE_FORK_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-baseline-components \
  ./scripts/build_lighthouse_component_fork.sh
```

By default, the build script detects the Lighthouse version from
`LIGHTHOUSE_BASE_IMAGE` and selects the matching component patch. On the current
baseline image this resolves to `v7.0.1`, matching the official baseline binary.
It also builds Lighthouse with the `maxperf` Cargo profile to match the current
baseline image, and enables the `portable,gnosis` Cargo features to match the
baseline `lighthouse --version` spec list.

Verify the fork image version after build:

```bash
docker run --rm handsonsecurity/seedemu-ethereum:pos2.0-baseline-components \
  lighthouse --version
```

The component-aware image supports:

```text
SEED_EMU_LIGHTHOUSE_COMPONENTS=cache
SEED_EMU_LIGHTHOUSE_COMPONENTS=light-client
SEED_EMU_LIGHTHOUSE_COMPONENTS=processor
SEED_EMU_LIGHTHOUSE_COMPONENTS=rayon
SEED_EMU_LIGHTHOUSE_COMPONENTS=network
SEED_EMU_LIGHTHOUSE_COMPONENTS=all
```

If the image already exists, you can skip this step.

## Run the Full Benchmark

Run the default full 300-beacon benchmark:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

RUN_ID=manual-fork-components-$(date +%Y%m%d-%H%M%S) \
  ./scripts/run_300_fork_component_benchmark.sh
```

The default command runs all variants serially. Each variant regenerates output,
starts the network, waits for PoS health, collects raw benchmark data for 10
hours, then shuts down and removes the containers before the next variant.

## Run Selected Variants

To run only one variant:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

RUN_ID=manual-cache-only-$(date +%Y%m%d-%H%M%S) \
VARIANTS="baseline fork_cache_only" \
  ./scripts/run_300_fork_component_benchmark.sh
```

To run only `fork_all`:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

RUN_ID=manual-fork-all-$(date +%Y%m%d-%H%M%S) \
VARIANTS="fork_all" \
  ./scripts/run_300_fork_component_benchmark.sh
```

## Useful Runtime Controls

Short smoke test:

```bash
RUN_ID=smoke-fork-components \
DURATION_SECONDS=600 \
VARIANTS="baseline fork_cache_only" \
  ./scripts/run_300_fork_component_benchmark.sh
```

Build the component image as part of the run:

```bash
BUILD_FORK_IMAGE=1 ./scripts/run_300_fork_component_benchmark.sh
```

Adjust transaction rate:

```bash
TX_RATE=2 ./scripts/run_300_fork_component_benchmark.sh
```

Adjust the receipt drain window after transaction sending stops:

```bash
TX_DRAIN_TIMEOUT=180 TX_DRAIN_INTERVAL=12 ./scripts/run_300_fork_component_benchmark.sh
```

The runner stops the transaction sender before stopping the receipt collector.
It then waits for pending receipts to drain, so transactions sent near the end
of the sampling window are not incorrectly counted as uncommitted.

Adjust sampled beacon count for propagation measurements:

```bash
CONSENSUS_BEACON_SAMPLE=48 ./scripts/run_300_fork_component_benchmark.sh
```

## Output Layout

Data is written under:

```text
experiments/large_scale/ethereum/experiments/fork-component-benchmark/<RUN_ID>/
```

Each variant has its own directory:

```text
baseline/
fork_cache_only/
fork_light_client_only/
fork_processor_only/
fork_rayon_only/
fork_quic_only/
fork_all/
```

Important raw files:

```text
experiment.env
memory_raw.csv
memory_by_type.csv
block_metrics.csv
tx-load/tx_sender.csv
tx_receipts.csv
consensus/beacon_head_samples.csv
consensus/block_first_seen.csv
consensus/finality_samples.csv
consensus/attestation_inclusions.csv
consensus/canonical_slot_observations.csv
consensus/reorg_events.csv
health-passed.log
health-final.log
docker-compose.yml
```

## Mapping to Paper Benchmark Metrics

Resource table:

```text
Max Stable BN:
  Determined by whether the 300-beacon variant completes the 10-hour run.

BN Mem. Avg / BN Mem. P95:
  Derived from memory_raw.csv or memory_by_type.csv.

Mem. Growth:
  Derived from the steady-state beacon memory time series.

CPU / BN P95:
  Derived from beacon rows in memory_raw.csv.
```

Robustness and timing table:

```text
Block Prop. P95:
  Derived from consensus/block_first_seen.csv across sampled beacon nodes.

Attest. Incl. P95:
  Derived from consensus/attestation_inclusions.csv.

Finality Lag P95:
  Derived from consensus/finality_samples.csv.

Tx Send Success:
  Derived from tx-load/tx_sender.csv.

Tx Commit P95:
  Derived from tx_receipts.csv.

Reorg Rate:
  Derived from consensus/reorg_events.csv and experiment duration.
```

## Notes

The runner uses `BUILD_MODE=nobuild-mount` by default, so generated per-node
Docker images are not built. The generated node files are mounted into shared
runtime images instead. This is required for practical 300-beacon iteration.

The runner always attempts to stop and remove containers after each variant. If
you pass `--keep-last`, the final variant is left running for manual inspection.
