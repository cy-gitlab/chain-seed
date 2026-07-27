# Validator-Worker Recovery Matrix

This experiment determines whether increasing Lighthouse beacon-processor
workers restores validator liveness and finality under the validator pressure
that degraded the two-worker configuration.

## Controlled Configuration

Every new run keeps the following values fixed:

- 300 Beacon nodes;
- 300 Geth nodes;
- 900 Validator client containers;
- 64 stub ASes;
- one transaction per second;
- ten hours of sampling after the health check passes;
- the same configurable Lighthouse image and `processor-workers` component.

The default new-run matrix is:

| Keys per Validator client | Active validators | Worker cap | Purpose |
| ---: | ---: | ---: |---|
| 1 | 900 | 2 | Rerun the baseline with validator-liveness collection |
| 4 | 3,600 | 4 | Test moderate recovery cost |
| 4 | 3,600 | 8 | Test additional processing headroom |
| 8 | 7,200 | 4 | Test recovery from the observed finality failure |
| 8 | 7,200 | 8 | Test stronger recovery from the observed finality failure |

The completed 3,600/worker=2 and 7,200/worker=2 runs are not rerun. They are
read from the existing validator-pressure dataset and inserted into the new
`summary.csv` with status `reference-existing`.

The independent variable called "active validators" is the number of validator
keys performing duties. The number of Validator client containers remains 900,
so this experiment does not measure the cost of adding more containers.

## Prerequisite

The configurable component image must already exist:

```bash
docker image inspect handsonsecurity/seedemu-ethereum:pos2.0-worker-cap >/dev/null
```

Validate the matrix without starting containers:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum
./scripts/run_validator_worker_recovery_matrix.sh --dry-run
```

The dry run must print these five points in order:

```text
1:2 4:4 4:8 8:4 8:8
```

## Full Experiment

Run the complete matrix manually:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum

SWEEP_ID=validator-worker-recovery-$(date +%Y%m%d-%H%M%S) \
MATRIX="1:2 4:4 4:8 8:4 8:8" \
BEACONS=300 \
STUB_AS_COUNT=64 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
HEALTH_TIMEOUT=7200 \
LIGHTHOUSE_COMPONENT_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0-worker-cap \
  ./scripts/run_validator_worker_recovery_matrix.sh
```

The five runs execute serially. Before each run, the underlying benchmark
regenerates `output/`, starts a clean network, waits for health, samples for ten
hours, drains transaction receipts, captures the final state, and executes
`docker compose down -v --remove-orphans`. Total wall-clock time will exceed 50
hours because network generation, startup, convergence, and cleanup are outside
the ten-hour measurement window.

Do not use `--continue-on-failure` for the official dataset. A nonzero exit code
indicates an operational failure and should stop the matrix before the next
configuration. Consensus degradation can still occur in a run with exit code
zero, so evaluate the metrics below rather than relying on `status=success`.

## Run One Point

To run only 7,200 active validators with worker cap 4:

```bash
SWEEP_ID=validator-worker-v7200-w4-$(date +%Y%m%d-%H%M%S) \
MATRIX="8:4" \
DURATION_SECONDS=36000 \
TX_RATE=1 \
HEALTH_TIMEOUT=7200 \
  ./scripts/run_validator_worker_recovery_matrix.sh
```

Use `MATRIX="4:4"`, `MATRIX="4:8"`, or `MATRIX="8:8"` for the other recovery
points. Add `--no-cap2-references` when a standalone result should not include
the existing comparison rows.

## Output

```text
experiments/validator-worker-recovery/<SWEEP_ID>/
  sweep.env
  sweep.log
  summary.csv
  runs/
    b300-vc900-v<ACTIVE>-k<KEYS>-workers<WORKERS>/
      fork_processor_workers_only/
```

Every run retains raw memory, CPU, block, transaction, head, attestation,
validator-liveness, finality, and reorganization samples. Inspect the compact
summary with:

```bash
column -s, -t experiments/validator-worker-recovery/<SWEEP_ID>/summary.csv | less -S
```

## Recovery Criteria

Compare worker caps only within the same active-validator level. The primary
signals are:

1. `validator_liveness_last10_avg_pct`: must remain above the two-thirds
   finality threshold and should recover toward or above the 1,800-validator
   reference value of 79.17%.
2. `finality_lag_end_epochs`: should end at two epochs, or at most three during
   a boundary transition.
3. `finalized_epochs_last_hour`: should be close to the nominal 9--10 epochs
   finalized per hour, rather than zero.
4. `finality_stagnation_end_sec`: should remain below two epochs (768 seconds),
   rather than the observed 6,381-second stall at 7,200 validators/worker=2.
5. `attest_incl_p95_slots`, missed-slot rate, head lag, block propagation, and
   reorganization rate must not materially worsen.
6. `mem_avg_gib` and `mem_p95_gib` quantify the memory paid for recovered
   processing capacity.

The desired operating point is the smallest worker cap that restores finality
and provides a clear liveness margin. Worker cap 8 is not preferable to cap 4
unless it produces a measurable consensus improvement that justifies its extra
memory.
