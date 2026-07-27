# Worker-Cap Sweep Runbook

This experiment keeps the Ethereum network fixed at 300 beacon nodes, 300 geth
nodes, and 900 validator clients. It changes only the Lighthouse beacon
processor worker cap. The goal is to find the smallest cap that preserves chain
health and transaction performance while keeping beacon memory low.

## 1. Build the configurable fork image

Run this once after changing the Lighthouse patch:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

LIGHTHOUSE_BASE_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0 \
LIGHTHOUSE_FORK_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-worker-cap \
  ./scripts/build_lighthouse_component_fork.sh
```

The build script uses TUNA Debian mirrors by default for the builder stage. If
that mirror is unavailable, override it explicitly:

```bash
APT_MIRROR=http://deb.debian.org/debian \
APT_SECURITY_MIRROR=http://deb.debian.org/debian-security \
LIGHTHOUSE_BASE_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0 \
LIGHTHOUSE_FORK_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-worker-cap \
  ./scripts/build_lighthouse_component_fork.sh
```

Check that the image exists:

```bash
docker image inspect handsonsecurity/seedemu-ethereum:pos2.0-worker-cap >/dev/null
```

## 2. Run one worker cap manually

Use this when you want full manual control and one run at a time:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

RUN_ID=worker-cap-4-$(date +%Y%m%d-%H%M%S) \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-worker-cap \
LIGHTHOUSE_PROCESSOR_WORKERS_CAP=4 \
VARIANTS="fork_processor_workers_only" \
BEACONS=300 \
STUB_AS_COUNT=64 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
  ./scripts/run_300_fork_component_benchmark.sh
```

Change `LIGHTHOUSE_PROCESSOR_WORKERS_CAP=4` to `1`, `2`, `8`, `16`, or any
other positive integer.

## 3. Run the serial sweep

This runs each cap sequentially and writes a summary CSV after every completed
or failed run:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

SWEEP_ID=worker-cap-$(date +%Y%m%d-%H%M%S) \
WORKER_CAPS="1 2 4 8 16" \
BEACONS=300 \
STUB_AS_COUNT=64 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-worker-cap \
  ./scripts/run_worker_cap_sweep.sh
```

If you want to continue after a failed cap:

```bash
STOP_ON_FAILURE=0 WORKER_CAPS="1 2 4 8 16" ./scripts/run_worker_cap_sweep.sh
```

## 4. Outputs

Sweep-level output:

```text
experiments/worker-cap-sweep/<SWEEP_ID>/summary.csv
experiments/worker-cap-sweep/<SWEEP_ID>/sweep.log
```

Per-run raw output:

```text
experiments/worker-cap-sweep/<SWEEP_ID>/runs/b300-workers<CAP>/fork_processor_workers_only/
```

Important files in each run directory:

```text
experiment.env
lighthouse-fork-summary.json
memory_raw.csv
memory_by_type.csv
block_metrics.csv
consensus/
tx-load/tx_sender.csv
tx_receipts.csv
tx_receipts_run_info.json
health-final.log
```

## 5. Decision rule

Use cap `2` as the current reference point. A better cap should either reduce
memory further or improve robustness without substantially increasing memory.
Reject a cap if it causes visible degradation in:

```text
block_prop_p95_sec
attest_incl_p95_slots
finality_lag_p95_epochs
tx_send_success_pct
committed_tps
tx_commit_p95_sec
reorg_rate_per_h
```

After identifying the best cap, use that value for the later maximum-scale
beacon-node experiment.

The original sweep uses periodic head polling and cannot resolve sub-second
block-processing differences. Before selecting the final cap, run the
event-stream experiment in `docs/RUNBOOK_WORKER_SIDE_EFFECTS.md`.
