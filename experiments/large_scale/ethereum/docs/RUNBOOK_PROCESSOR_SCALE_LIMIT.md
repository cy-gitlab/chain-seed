# Ethereum Processor-Only Scale Limit Runbook

This runbook measures how many Ethereum PoS beacon nodes the machine can run
when only the Lighthouse processor optimization is enabled.

## Experiment Goal

The single-variable fork benchmark shows that `fork_processor_only` is the only
component with a large memory effect. The next question is the scale limit:

```text
How many beacon nodes can run stably when:
  geth nodes = beacon nodes
  validator clients = 3 * beacon nodes
  transaction load is enabled
  only the processor optimization is enabled
```

The sweep runs one scale level at a time. Each level regenerates the SEED output,
starts the full network, waits for PoS health, sends continuous transactions,
collects raw memory/consensus/transaction data, then stops and removes the
containers before the next level.

## Recommended Two-Phase Procedure

Use a short discovery sweep first. This finds the approximate failure range
without spending 10 hours per level:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

SWEEP_ID=processor-discovery-$(date +%Y%m%d-%H%M%S) \
LEVELS="300 400 500 600 700 800" \
DURATION_SECONDS=3600 \
TX_RATE=1 \
  ./scripts/run_processor_scale_limit_sweep.sh
```

After discovery, validate the largest successful level and the next candidate
with the paper-quality 10-hour duration. For example, if `600` succeeds and
`700` fails in discovery:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

SWEEP_ID=processor-10h-confirm-$(date +%Y%m%d-%H%M%S) \
LEVELS="600 700" \
DURATION_SECONDS=36000 \
TX_RATE=1 \
  ./scripts/run_processor_scale_limit_sweep.sh
```

If the first failed level should not stop the sweep:

```bash
STOP_ON_FAILURE=0 ./scripts/run_processor_scale_limit_sweep.sh
```

or:

```bash
./scripts/run_processor_scale_limit_sweep.sh --continue-on-failure
```

For very large levels, startup can be slow. If a level fails only because the
health check times out, rerun that same level with a larger timeout before
counting it as a real scale failure:

```bash
HEALTH_TIMEOUT=14400 \
LEVELS="700" \
DURATION_SECONDS=3600 \
  ./scripts/run_processor_scale_limit_sweep.sh
```

## Output

Sweep data is written under:

```text
experiments/processor-scale-limit/<SWEEP_ID>/
```

Important files:

```text
sweep.log
summary.csv
runs/b<N>-processor/fork_processor_only/
```

Each scale-level directory contains the same raw files as the fork component
benchmark:

```text
memory_raw.csv
memory_by_type.csv
tx-load/tx_sender.csv
tx_receipts.csv
consensus/block_first_seen.csv
consensus/attestation_inclusions.csv
consensus/finality_samples.csv
health-passed.log
health-final.log
docker-compose.yml
```

`summary.csv` records:

```text
beacons
stub_as_count
status
exit_code
memory metrics
consensus timing metrics
transaction success and commit latency
reorg rate
```

## Notes

The script automatically chooses a conservative `STUB_AS_COUNT` for each beacon
scale. You can override it manually, but it must stay within the generator limit
of `100` stub ASes.

The experiment keeps the transaction workload enabled by default (`TX_RATE=1`).
Set `TX_RATE=0` only for a resource-only capacity test; do not use that result
for the paper's transaction-loaded scalability table.

The runner always shuts down containers after each level. If a level fails, the
latest generated output remains under `ethereum/output/` for debugging, but
containers should already have been removed by the child runner's cleanup trap.

At the end of each level, the runner stops the transaction sender first and
keeps the receipt collector alive for a short drain window. Override
`TX_DRAIN_TIMEOUT` or `TX_DRAIN_INTERVAL` if a large run needs more time for
the last accepted transactions to be observed on chain.
