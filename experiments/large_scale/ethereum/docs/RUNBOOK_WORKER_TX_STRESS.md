# Worker Mixed-Transaction Stress Experiment

## Purpose

This experiment tests whether a small Lighthouse Beacon Processor worker cap
degrades earlier when execution blocks become larger and more expensive to
process. The causal variable is the worker cap. Worker caps 2, 4, and 8 use the
same image, patch, topology, accounts, random seed, transaction sequence, and
load schedule.

Each formal run uses:

```text
300 Beacon nodes
300 Geth nodes
900 Validator clients and active validators
64 stub ASes
200 prefunded transaction accounts
64 Beacon head-event observers
32 Beacon Processor metric targets
10 hours of phased transaction load
128 MiB Geth cache per execution node
256 MiB Geth Go heap target
512 MiB Geth cgroup memory limit with swap disabled
EtherView disabled
```

The experiment must run worker caps serially. Running caps concurrently would
make host-level CPU, memory, disk, and Docker contention part of the treatment.

All worker-cap runs use the same Geth memory profile. `--cache` bounds Geth's
internal caches, `GOMEMLIMIT` makes the Go garbage collector target a bounded
heap, and the cgroup limit also bounds database page cache charged to the
container. The cgroup limit disables swap so an unsafe profile fails with a
recorded container OOM instead of silently thrashing the host. These settings
are infrastructure controls, not worker-cap treatments.

## Transaction profile

The default profile is `configs/tx_mix_worker_v1.json`:

| Transaction class | Weight | Purpose |
|---|---:|---|
| Native ETH transfer | 35% | Minimum-size, 21,000-gas reference |
| Storage update contract call | 50% | Common state-changing contract work |
| 1 KiB calldata contract call | 14% | Larger execution payload and block bytes |
| Small contract deployment | 1% | Contract creation and state growth |

The profile is a controlled workload mix, not a claim that these percentages
exactly reproduce Ethereum Mainnet. The same fixed random seed reproduces the
class selection and account selection in every worker-cap run.

A small storage contract is deployed before sampling starts. Load transactions
are broadcast without waiting for confirmation. Gas price is refreshed
periodically and kept above the RPC suggestion so that a rising base fee is not
mistaken for a worker bottleneck.

## Ten-hour schedule

| Phase | Target TPS | Duration |
|---|---:|---:|
| `baseline` | 1 | 120 min |
| `load25` | 25 | 45 min |
| `recovery25` | 1 | 30 min |
| `load50` | 50 | 45 min |
| `recovery50` | 1 | 30 min |
| `load75` | 75 | 45 min |
| `recovery75` | 1 | 30 min |
| `load100` | 100 | 45 min |
| `recovery100` | 1 | 210 min |

Total duration is exactly 36,000 seconds per worker cap. Recovery phases reveal
whether queues drain and finality returns after each load level. The schedule
stops at 100 target TPS because the 300-node testbed already saturates below
that target. Higher targets reduce the achieved send rate while accumulating
execution-state memory, so they do not add a useful worker-cap treatment.

## Build and verify

Run from:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum
```

Configure the host once. This raises the host-wide inotify instance limit used
by the 300 root-owned Geth containers:

```bash
scripts/configure_large_scale_host.sh
```

The failed run left the 8 GiB swap file full. With the experiment containers
stopped and ample `MemAvailable`, reset it once before the next formal run:

```bash
free -h
sudo swapoff -a
sudo swapon -a
free -h
```

The generated Compose file explicitly applies `nofile=524288` to every service.
The run aborts before sampling if the host inotify setting or sampled container
limits are lower than required.

The existing component image can be reused. Rebuild only if the Lighthouse
patch or its base image changed:

```bash
scripts/build_lighthouse_component_fork.sh
```

Verify it before the formal run:

```bash
scripts/verify_lighthouse_component_image.sh \
  handsonsecurity/seedemu-ethereum:pos2.0-baseline-components
```

## Optional short infrastructure check

This checks setup deployment, phase switching, block-based inclusion tracking,
Processor metrics, event streams, summarization, and cleanup. It is not paper
data:

```bash
SWEEP_ID="$(date +%Y%m%d-%H%M%S)-worker-tx-smoke" \
BEACONS=20 \
STUB_AS_COUNT=10 \
BLOCK_EVENT_BEACON_SAMPLE=8 \
PROCESSOR_METRICS_BEACON_SAMPLE=8 \
TX_ACCOUNT_COUNT=40 \
TX_LOAD_PHASES="baseline:1:300,load10:10:600,recovery10:1:300" \
  scripts/run_worker_tx_stress_sweep.sh --worker-caps "2"
```

## Required per-Geth memory validation

First verify the hard limit with 20 nodes. Every full Geth executes the same
blocks, so this checks the per-node 50 TPS working set without paying the
300-node startup cost:

```bash
BLOCK_EVENT_BEACON_SAMPLE=8 \
PROCESSOR_METRICS_BEACON_SAMPLE=8 \
SWEEP_ID="$(date +%Y%m%d-%H%M%S)-worker-tx-gethmem512-b20-check" \
  scripts/run_worker_tx_stress_sweep.sh \
    --worker-caps "2" \
    --beacons 20 \
    --stub-as-count 10 \
    --geth-cache-mb 128 \
    --geth-go-mem-limit-mb 256 \
    --geth-memory-max-mb 512 \
    --phases "baseline:1:900,load50:50:3600,recovery50:1:900"
```

Proceed only if all transactions commit, finality remains healthy, and
`memory_oom_kill_total` remains zero. Then repeat the same 90-minute validation
at 300 nodes:

```bash
SWEEP_ID="$(date +%Y%m%d-%H%M%S)-worker-tx-gethmem512-b300-check" \
  scripts/run_worker_tx_stress_sweep.sh \
    --worker-caps "2" \
    --geth-cache-mb 128 \
    --geth-go-mem-limit-mb 256 \
    --geth-memory-max-mb 512 \
    --phases "baseline:1:900,load50:50:3600,recovery50:1:900"
```

Do not lower the 96 GiB host guard to force completion.

## Formal manual run

Choose one shared experiment ID:

```bash
export SWEEP_ID="$(date +%Y%m%d-%H%M%S)-worker-tx-stress-300"
```

Run worker cap 2 and inspect it before continuing:

```bash
scripts/run_worker_tx_stress_sweep.sh \
  --worker-caps "2" \
  --geth-cache-mb 128 \
  --geth-go-mem-limit-mb 256 \
  --geth-memory-max-mb 512
```

Run caps 4 and 8 later with the same `SWEEP_ID`:

```bash
scripts/run_worker_tx_stress_sweep.sh --worker-caps "4"
scripts/run_worker_tx_stress_sweep.sh --worker-caps "8"
```

To run all three automatically but still serially:

```bash
scripts/run_worker_tx_stress_sweep.sh \
  --worker-caps "2 4 8"
```

Do not use `--build-fork-image` unless the reusable image actually needs to be
rebuilt. The benchmark regenerates `output/` for every cap and removes all
containers and volumes after every run. It also records `host_resources.csv`
every ten seconds and stops cleanly before available host memory falls below
96 GiB. Set `HOST_MIN_AVAILABLE_GIB` only if the machine's memory reservation
policy changes.

## Raw and derived data

Sweep-level outputs:

```text
experiments/worker-tx-stress/<SWEEP_ID>/runs.csv
experiments/worker-tx-stress/<SWEEP_ID>/tx_stress_phase_summary.csv
experiments/worker-tx-stress/<SWEEP_ID>/tx_type_phase_summary.csv
experiments/worker-tx-stress/<SWEEP_ID>/processor_tx_phase_summary.csv
experiments/worker-tx-stress/<SWEEP_ID>/sweep.log
```

Important per-run raw data:

```text
tx-load/tx_sender.csv
tx-load/tx_phases.csv
tx-load/tx_phase_state.json
tx-load/setup.json
tx_receipts.csv
tx_receipts_run_info.json
block_metrics.csv
block-visibility/head_events.csv
processor-metrics/processor_metrics.csv.gz
memory_raw.csv
memory_by_type.csv
geth-memory-summary.json
experiment.env
consensus/
```

`memory_raw.csv` and `memory_by_type.csv` include cgroup anonymous memory,
file-backed page cache, kernel memory, and OOM/OOM-kill counters. A run with a
non-zero Geth OOM-kill count is invalid even if the remaining nodes keep
producing blocks.

High-rate inclusion is detected by scanning each new block and its receipt set.
It does not issue one periodic receipt RPC per pending transaction, because that
would add a second artificial Geth workload at the same order as the benchmark.

The formal schedule bounds the transaction count so the comparison stresses
full execution blocks without reproducing the previous host OOM.

## Interpretation

A worker-cap side effect is supported only if cap 2 degrades at a lower load
than caps 4 or 8 and the larger cap restores both internal and external metrics.
The strongest evidence is:

```text
cap 2 worker saturation or queue growth
followed by slower block visibility, missed slots, or finality degradation
while cap 4 or 8 remains healthy at the same phase
```

Transaction rejection alone is insufficient. If every cap loses transaction
throughput at the same rate, the limiting component is more likely Geth,
transaction-pool capacity, host CPU, disk, or the block gas limit.
