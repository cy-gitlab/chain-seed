# Ethereum Processor Subcomponent Benchmark Runbook

This runbook splits the previous `fork_processor_only` optimization into three
single-variable experiments. Run them one by one manually.

## Goal

Keep the whole experiment identical to the existing 300-beacon fork benchmark,
but enable only one processor subcomponent at a time:

- `fork_processor_workers_only`: only limit beacon processor worker count.
- `fork_processor_queues_only`: only limit beacon processor backlog queues.
- `fork_processor_gossip_only`: only limit gossip batch sizes.

Fixed experiment settings:

- `300` beacon nodes.
- `300` geth nodes.
- `900` validator clients.
- `10` hours after health check passes.
- Transaction workload enabled.
- Beacon historic state reconstruction enabled.
- Lighthouse CLI cache profile unchanged.
- Beacon peer settings unchanged.

This isolates which part of the previous processor optimization reduced memory.

## Build the Split-Processor Fork Image

Build a new image tag so this experiment does not overwrite the older component
benchmark image:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

LIGHTHOUSE_BASE_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0 \
LIGHTHOUSE_FORK_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-processor-split \
  ./scripts/build_lighthouse_component_fork.sh
```

Verify the built Lighthouse binary:

```bash
docker run --rm handsonsecurity/seedemu-ethereum:pos2.0-processor-split \
  lighthouse --version
```

The important point is that this image must be built after the processor split
patch is present. If an older image is used, the new subcomponent tokens will be
ignored by Lighthouse.

## Run Workers-Only

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

RUN_ID=processor-workers-only-$(date +%Y%m%d-%H%M%S) \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-processor-split \
VARIANTS="fork_processor_workers_only" \
BEACONS=300 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
  ./scripts/run_300_fork_component_benchmark.sh
```

## Run Queues-Only

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

RUN_ID=processor-queues-only-$(date +%Y%m%d-%H%M%S) \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-processor-split \
VARIANTS="fork_processor_queues_only" \
BEACONS=300 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
  ./scripts/run_300_fork_component_benchmark.sh
```

## Run Gossip-Only

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

RUN_ID=processor-gossip-only-$(date +%Y%m%d-%H%M%S) \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-processor-split \
VARIANTS="fork_processor_gossip_only" \
BEACONS=300 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
  ./scripts/run_300_fork_component_benchmark.sh
```

## Optional Full-Processor Repeat

The old `fork_processor_only` result can be used as the reference for the
combined processor optimization. If you want strict same-image comparability,
rerun it with the split image:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

RUN_ID=processor-full-repeat-$(date +%Y%m%d-%H%M%S) \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-processor-split \
VARIANTS="fork_processor_only" \
BEACONS=300 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
  ./scripts/run_300_fork_component_benchmark.sh
```

## Output

Each manual run writes data under:

```text
experiments/large_scale/ethereum/experiments/fork-component-benchmark/<RUN_ID>/<variant>/
```

Important raw data files:

```text
memory_raw.csv
memory_by_type.csv
block_metrics.csv
tx-load/tx_sender.csv
tx_receipts.csv
consensus/block_first_seen.csv
consensus/attestation_inclusions.csv
consensus/finality_samples.csv
consensus/reorg_events.csv
experiment.env
lighthouse-fork-summary.json
```

After each run, check `experiment.env` and `lighthouse-fork-summary.json` to
confirm the intended component token was used.
