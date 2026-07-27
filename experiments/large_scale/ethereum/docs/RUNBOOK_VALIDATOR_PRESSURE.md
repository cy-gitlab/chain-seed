# Ethereum Validator-Pressure Benchmark

This experiment measures how the two-worker Lighthouse configuration behaves as
the number of active validators increases. It keeps the network and container
scale fixed at:

- 300 Beacon nodes;
- 300 Geth nodes;
- 900 Validator client containers;
- 64 stub ASes;
- two beacon-processor workers per Beacon node;
- one transaction per second.

Pressure is changed only through the number of genesis validator keys loaded by
each Validator client. The default matrix is:

| Keys per client | Validator clients | Active validators |
| ---: | ---: | ---: |
| 1 | 900 | 900 (reuse existing worker=2 run) |
| 2 | 900 | 1,800 |
| 4 | 900 | 3,600 |
| 8 | 900 | 7,200 |

This avoids adding thousands of containers, so differences primarily represent
additional consensus duties rather than container-count overhead.

## Prerequisite

The configurable Lighthouse image from the worker-cap experiment must exist:

```bash
docker image inspect handsonsecurity/seedemu-ethereum:pos2.0-worker-cap >/dev/null
```

No image rebuild is required. Validate the matrix before a long run:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum
./scripts/run_validator_pressure_sweep.sh --dry-run
```

## Full 10-Hour Sweep

The following command reuses the existing 900-validator, worker=2 ten-hour run,
then runs the three new levels serially. Each new level regenerates the
output, starts a clean network, samples for ten hours after health passes, drains
transaction receipts, and removes all containers and volumes before continuing.

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

SWEEP_ID=validator-pressure-$(date +%Y%m%d-%H%M%S) \
VALIDATOR_KEYS_PER_CLIENTS="2 4 8" \
BEACONS=300 \
STUB_AS_COUNT=64 \
WORKER_CAP=2 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
HEALTH_TIMEOUT=7200 \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-worker-cap \
  ./scripts/run_validator_pressure_sweep.sh
```

The total wall-clock time is longer than 30 hours because each level also needs
topology generation, genesis construction, startup, health convergence, receipt
draining, and cleanup.

## Run One Level Manually

Set `VALIDATOR_KEYS_PER_CLIENTS` to a single value. For example, this runs the
1,800-validator level:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

SWEEP_ID=validator-pressure-v1800-$(date +%Y%m%d-%H%M%S) \
VALIDATOR_KEYS_PER_CLIENTS="2" \
BEACONS=300 \
STUB_AS_COUNT=64 \
WORKER_CAP=2 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
HEALTH_TIMEOUT=7200 \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-worker-cap \
  ./scripts/run_validator_pressure_sweep.sh
```

Use `4` for 3,600 active validators or `8` for 7,200. The script stops after a
failed level by default. `--continue-on-failure` is available for diagnostic
sweeps, but it is not recommended for the official experiment.

## Optional Two-Hour Screening

Use this only to find whether the high levels can start and remain healthy before
committing to the official ten-hour runs:

```bash
SWEEP_ID=validator-pressure-screen-$(date +%Y%m%d-%H%M%S) \
VALIDATOR_KEYS_PER_CLIENTS="2 4 8" \
REUSE_BASELINE=0 \
DURATION_SECONDS=7200 \
HEALTH_TIMEOUT=7200 \
  ./scripts/run_validator_pressure_sweep.sh
```

Do not mix screening results with the ten-hour paper dataset.

If the existing worker=2 baseline data is unavailable, run all four levels with
`REUSE_BASELINE=0 VALIDATOR_KEYS_PER_CLIENTS="1 2 4 8"`.

## Collected Data

Results are written below:

```text
experiments/validator-pressure/<SWEEP_ID>/
  sweep.env
  sweep.log
  summary.csv
  runs/
    b300-vc900-v<ACTIVE>-k<KEYS>/fork_processor_workers_only/
```

Each run retains all raw memory, block, transaction, head, finality,
attestation, reorganization, and validator-liveness data. In particular:

```text
consensus/validator_liveness.csv
consensus/beacon_head_samples.csv
consensus/attestation_inclusions.csv
consensus/finality_samples.csv
consensus/reorg_events.csv
```

`summary.csv` includes resource measurements, block propagation P95/P99, head
lag P95/P99/maximum, percentages of rounds exceeding 1/4/8 slots, validator
liveness average/P05/minimum, maximum missed validators, attestation inclusion,
finality, missed-slot rate, transaction commitment, and reorganization rate.

The main degradation signals are:

- validator liveness falling below 100%;
- attestation inclusion moving above one slot;
- head-lag tails or block propagation increasing;
- missed slots, finality lag, or reorganizations appearing;
- transactions no longer reaching the offered one TPS.

Inspect the compact result after the sweep:

```bash
column -s, -t experiments/validator-pressure/<SWEEP_ID>/summary.csv | less -S
```
