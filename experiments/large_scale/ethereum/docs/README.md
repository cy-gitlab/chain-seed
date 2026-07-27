# D61 Ethereum Beacon Memory Experiments

This directory contains the curated D61 Ethereum PoS large-scale memory experiments.
It keeps only the scripts and data needed to reproduce the current 10-beacon and
300-beacon single-variable comparison results.

## Layout

```text
ethereum/
  src/          SEED emulation generator
  scripts/      experiment runners, output patchers, collectors, tx sender
  docs/         README and runbook
  experiments/  curated historical experiment data
  fidelity/     static Xatu Beacon P2P topology reconstruction
  output/       current generated compose output, if an experiment is running
```

## Preserved Data

The curated data is under:

```text
experiments/10-beacon-single-variable/
experiments/300-beacon-single-variable/
```

Each variant directory keeps the raw CSV/log/env files from the run. Each scale
also has a regenerated `summary.csv` and `summary.md`.

Kept 10-beacon variants:

```text
baseline_all
no_historic
low_cache
low_peers
lighthouse_fork
```

Kept 300-beacon variants:

```text
baseline_all
no_historic
low_cache_only
low_peers_only
lighthouse_fork
```

All preserved variants have transaction load in `block_metrics.csv`. Failed or
empty-block-only runs were removed.

## Main Results

For the 300-beacon transaction-loaded runs:

```text
official baseline_all avg beacon memory: 0.9648 GiB
lighthouse_fork avg beacon memory:      0.5804 GiB
```

The Lighthouse fork profile reduces average steady beacon memory by about
`0.3844 GiB` per beacon in the 300-beacon run.

## Notes

The Lighthouse fork is activated only for `lighthouse bn` processes through:

```text
SEED_EMU_LIGHTHOUSE_PROFILE=lite
SEED_EMU_LIGHTHOUSE_RAYON_THREADS=2
RAYON_NUM_THREADS=2
```

Validator clients are not patched by the fork-output patcher.

For the processor-only maximum-scale experiment, see
`docs/RUNBOOK_PROCESSOR_SCALE_LIMIT.md`.

For the processor subcomponent single-variable experiments, see
`docs/RUNBOOK_PROCESSOR_SUBCOMPONENT_BENCHMARK.md`.

For the worker-cap tuning experiment, see
`docs/RUNBOOK_WORKER_CAP_SWEEP.md`.

For the validator-pressure experiment with a fixed Validator client container
count, see `docs/RUNBOOK_VALIDATOR_PRESSURE.md`.

For the follow-up worker-cap recovery matrix at 3,600 and 7,200 active
validators, including the fully instrumented 900-validator rerun, see
`docs/RUNBOOK_VALIDATOR_WORKER_RECOVERY.md`.

For the paired API-contention experiment comparing Beacon Processor worker caps
2, 4, and 8, see `docs/RUNBOOK_API_WORKER_CONTENTION.md`.

For the archived worker-side-effect roadmap and the high-resolution block
visibility experiment, see `docs/RUNBOOK_WORKER_SIDE_EFFECTS.md`.

For the formal mixed-transaction and block-fullness worker stress experiment,
see `docs/RUNBOOK_WORKER_TX_STRESS.md`.

For static Beacon P2P topology extraction from public Xatu data, see
`fidelity/docs/RUNBOOK.md`. That pipeline is independent from the memory
experiments and does not start containers.
