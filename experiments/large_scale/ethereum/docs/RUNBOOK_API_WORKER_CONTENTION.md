# API Worker Contention Experiment

## Question

This experiment tests whether a small Lighthouse Beacon Processor worker cap
reduces API capacity or interferes with consensus processing under concurrent
read-only Beacon API traffic.

The causal comparison changes only:

```text
Beacon Processor worker cap: 2, 4, or 8
```

Every run otherwise uses:

```text
300 Beacon nodes
300 Geth nodes
900 Validator clients and active validators
historic state reconstruction enabled on every Beacon
default Lighthouse cache and peer settings
1 transaction/second background load
EtherView disabled during the controlled API experiment
```

`no_historic` is deliberately excluded. It can be tested later as a mitigation,
after worker causality is established.

## Paired Design

The experiment selects 30 stub ASes. Within each selected AS:

```text
one Beacon is the API-loaded target
one Beacon is the unloaded control
```

This controls for emulated network location. Eight target/control pairs are
sampled every two seconds for Beacon Processor metrics and local head lag. A
third Beacon outside both cohorts supplies global finality and attestation data.

The API profile is `configs/api_observer_read_v1.json`:

| Request | Weight |
|---|---:|
| Head header | 35% |
| Head block | 25% |
| Head finality checkpoints | 25% |
| Active validators at head | 15% |

The profile is a controlled D61 observer workload. It can later be replaced by
an API mix derived from an EtherView request trace without changing the runner.

## Default Schedule

The rate is per target Beacon. With 30 targets, the total configured rates are:

| Phase | Duration | Per-node rate | Total rate |
|---|---:|---:|---:|
| `baseline` | 30 min | 0 | 0 req/s |
| `load_1x` | 30 min | 0.2 req/s | 6 req/s |
| `recovery_1x` | 10 min | 0 | 0 req/s |
| `load_5x` | 30 min | 1 req/s | 30 req/s |
| `recovery_5x` | 10 min | 0 | 0 req/s |
| `load_10x` | 30 min | 2 req/s | 60 req/s |
| `recovery_10x` | 10 min | 0 | 0 req/s |
| `load_20x` | 30 min | 4 req/s | 120 req/s |

One worker-cap run therefore samples for three hours, followed by a 60-second
drain window. This is a threshold-finding experiment, not yet the final 10-hour
confirmation run.

## Preflight

Run from:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum
```

Build the component image after every Lighthouse patch change. The build cache
is reused, but the image receives the exact patch SHA as a label:

```bash
scripts/build_lighthouse_component_fork.sh
```

Verify both the patch SHA label and the dynamic worker-cap marker before running:

```bash
scripts/verify_lighthouse_component_image.sh \
  handsonsecurity/seedemu-ethereum:pos2.0-baseline-components
```

The experiment runner regenerates `output/`, starts the network, waits for the
existing PoS health check, samples the experiment, and always runs
`docker compose down -v --remove-orphans` on completion or interruption.

Before sampling, it also requires all of the following:

```text
the fork image patch SHA matches the current source patch
the Lighthouse binary supports the dynamic worker-cap variable
active worker metrics never exceed the requested cap
300 Beacon, 300 Geth, and 900 Validator processes are running
the discovered Geth JSON-RPC endpoint responds with chain ID 1337
the transaction sender can initialize all funded accounts
```

If a client startup has fallen back to `tail` or `sleep` while its container
still reports `Up`, the preflight restarts only that compose service and checks
the exact process counts again.

During sampling, any required collector or transaction sender that exits early
causes the run to fail and triggers container cleanup.

## Short Infrastructure Check

This verifies the full experiment path without producing paper data:

```bash
BEACONS=20 \
STUB_AS_COUNT=10 \
API_TARGET_COUNT=5 \
API_CONTROL_COUNT=5 \
API_MONITOR_PER_ROLE=2 \
API_LOAD_PHASES='baseline:0:120,load:1:180,recovery:0:60,load_high:4:180' \
SWEEP_ID="$(date +%Y%m%d-%H%M%S)-api-smoke" \
scripts/run_api_worker_contention_sweep.sh --worker-caps '2'
```

Do not combine smoke-test output with the 300-Beacon results.

## Run One Worker Cap at a Time

Use one stable experiment ID for all three manual runs:

```bash
export SWEEP_ID="$(date +%Y%m%d-%H%M%S)-api-worker-300-rerun"
```

Run worker cap 2:

```bash
scripts/run_api_worker_contention_sweep.sh --worker-caps '2'
```

After inspecting that run, execute cap 4 and cap 8 separately:

```bash
scripts/run_api_worker_contention_sweep.sh --worker-caps '4'
scripts/run_api_worker_contention_sweep.sh --worker-caps '8'
```

Alternatively, run all caps serially:

```bash
scripts/run_api_worker_contention_sweep.sh --worker-caps '2 4 8'
```

Do not run different caps concurrently. They must use the same machine and be
executed serially so that host resource contention is comparable.

## Ten-Hour Confirmation

After the threshold experiment identifies a rate that separates worker cap 2
from a larger cap, confirm that rate for ten hours. For example, to test 2
requests/second per target Beacon with one hour of baseline and nine hours of
load:

```bash
export API_LOAD_PHASES='baseline:0:3600,load_boundary:2:32400'
export SWEEP_ID="$(date +%Y%m%d-%H%M%S)-api-worker-boundary-10h"
scripts/run_api_worker_contention_sweep.sh --worker-caps '2'
scripts/run_api_worker_contention_sweep.sh --worker-caps '4'
```

Replace `2` with the boundary rate found by the first experiment.

## Output

Data is written under:

```text
experiments/api-worker-contention/<SWEEP_ID>/runs/
```

Each worker-cap run preserves:

```text
api-load/api_requests.csv.gz             raw request result and latency
api-load/api_phases.csv                  exact phase boundaries
api-experiment/api_cohorts.csv           target/control assignment
processor-metrics/processor_metrics.csv.gz
consensus/beacon_head_samples.csv
memory_raw.csv
tx-load/tx_sender.csv
tx_receipts.csv
execution-rpc-preflight/
node-process-preflight/
```

It also derives, without deleting raw data:

```text
api_phase_summary.csv
processor_phase_summary.csv
cohort_phase_summary.csv
```

## Interpretation

Worker contention is supported only when the same API phase shows all of the
following:

```text
cap 2 reaches worker saturation and accumulates API or consensus work
loaded targets degrade relative to same-AS controls
cap 4 or 8 reduces the internal backlog and restores the external metric
```

If only API latency worsens, the side effect is limited API capacity. If cap 4
or 8 does not recover the result, the bottleneck is more likely CPU, database,
disk, or another shared resource rather than the worker cap.

## Completed result

The completed rerun is:

```text
experiments/api-worker-contention/20260723-101943-api-worker-300-rerun/
```

All three worker caps completed successfully. At the peak total rate of 120
requests/second:

| Workers | Beacon memory avg/P95 | API latency P95 | API success |
|---:|---:|---:|---:|
| 2 | 0.42 / 0.46 GiB | 164.8 ms | 100% |
| 4 | 0.45 / 0.50 GiB | 238.9 ms | 100% |
| 8 | 0.47 / 0.54 GiB | 174.0 ms | 100% |

Every cap retained 100% steady-state validator liveness, a two-epoch finality
lag, one-slot P95 attestation inclusion, and 100% commitment of 10,859 submitted
transactions. No API, transport, client-queue, missed-slot, or reorg failure was
observed. Loaded Beacon nodes also remained at zero-slot P95 head lag relative
to same-AS controls.

The cap-4 API latency is non-monotonic: it is higher at low load and falls as
the configured rate increases. It is therefore treated as run-level noise, not
as a worker effect. The experiment does not support ordinary observer API
traffic as a side effect of cap 2 up to the tested rate.

The old consensus poller did reveal that cap-2 nodes more frequently exposed a
new block one polling round later than larger caps. Because one round takes
approximately 2.6 seconds, that result is a coarse signal rather than a precise
delay measurement. The follow-up event-stream experiment is documented in
`docs/RUNBOOK_WORKER_SIDE_EFFECTS.md`.
