# Solana Large-Scale Runbook

Run all commands from:

```bash
cd ~/chain-seed/experiments/large_scale/solana
```

## Current Main Goal

The current Solana goal is a voting-validator consensus experiment:

- All initial validators are prepared before startup.
- Every validator's identity, vote account, and stake account are written into
  genesis.
- After the network is up, every validator should be current and voting.
- Dynamic validator joining is not part of the main experiment; keep it only as
  a separate optional comparison.
- The generated topology includes an AS2/IX100 anchor provider peering for every
  service stub AS. This is required for large all-voting runs because the
  complex primary/secondary transit graph alone did not guarantee that every
  stub AS learned the bootstrap AS150 prefix.

`scripts/run_solana_large_experiment.sh` now defaults to this mode with
`SOLANA_GENESIS_VOTING_VALIDATORS=1`.

## Host Network Tuning

Before 200+ all-voting runs, raise the host UDP/socket buffers. The default
Linux values around 212 KiB are too small for hundreds of Agave gossip, repair,
Turbine, and TPU sockets and have produced `rcvbuf_errors`/`sndbuf_errors` in
failed 200-validator runs.

This is runtime-only tuning; it does not edit `/etc/sysctl.conf`.

```bash
sudo scripts/solana_host_tuning.sh apply
scripts/solana_host_tuning.sh show
```

Restore the previous values after experiments if needed:

```bash
sudo scripts/solana_host_tuning.sh restore
```

## All-Voting Genesis-Preseeded Mode

This is the default and recommended mode for current large-scale tests:

- `SOLANA_GENESIS_VOTING_VALIDATORS=1`: patch generated output so every
  validator's identity, vote account, and stake account are included in genesis.
- Non-bootstrap validators skip runtime `airdrop`, `create-vote-account`, and
  `delegate-stake`; they start directly with their preseeded vote account.
- Bootstrap temporarily initializes the genesis blockstore, publishes a shared
  `genesis-ledger.tar`, and then all validators unpack that full ledger before
  the synchronized startup barrier. This is required because sharing only
  `genesis.bin` makes Agave 2.2 non-bootstrap validators panic with
  `InvalidBlock(Incomplete)` while loading bank 0.
- The run script automatically disables observer mode and dynamic stake patching
  in this mode.
- Genesis voting mode defaults to a strongly weighted staged-start stake set:
  bootstrap gets 100 SOL, the first 31 non-bootstrap validators get 1000 SOL
  each, and the remaining validators get 0.05 SOL each. For `CORE_VALIDATORS=1`
  this still gives the bootstrap plus core validator enough active stake to
  start producing slots before the remaining validators are admitted.
- Non-bootstrap validators keep voting enabled but are patched as lightweight
  private RPC nodes: `--full-rpc-api` and
  `--enable-rpc-transaction-history` are removed, and `--private-rpc` is added.
  Use bootstrap as the RPC and transaction entry point.
- If not overridden, the run script sets `STRICT_VALIDATOR_STATUS=0`,
  `MIN_VOTING_VALIDATORS=0`, and leaves `MAX_DELINQUENT_VALIDATORS` unset for
  genesis-voting runs. The `solana validators` current/delinquent counts are
  still logged, but they are not a startup gate because they can lag staged
  startup even when slots are advancing.
- If not overridden, the run script also sets `STRICT_ACTIVE_SAMPLING=1` and
  `SLOT_STALL_TIMEOUT=600`, so a run fails if all validators stay alive but
  slots stop advancing.
- `SOLANA_DISABLE_WARMUP_EPOCHS=1` is the default. Keep it enabled when testing
  long `SOLANA_SLOTS_PER_EPOCH` values; otherwise Solana's warmup epochs still
  begin with short 32/64-slot epochs.
- For 200+ validators, use staged startup:
  `SOLANA_GENESIS_STAGED_STARTUP=1` and a small
  `SOLANA_GENESIS_CORE_VALIDATORS` value. The bootstrap plus core validators
  first form an advancing chain; the remaining genesis voting validators are
  then admitted in controlled batches.

This is the correct mode for experiments where non-voting validators would be
meaningless.

If final health stalls below the expected active validator count and affected
containers repeatedly log `waiting for entrypoint host 10.150.0.71 to be
reachable`, check the generated topology first. It means those ASes lack a
route to the bootstrap prefix; it is not a voting-flag issue.

## Legacy Observer Process-Scale Baseline

The older stable process-scale path is bootstrap-only consensus plus non-voting
observer validators. Use it only when the question is how many
`agave-validator` processes can stay alive, not whether all validators are
voting:

- `SOLANA_GENESIS_VOTING_VALIDATORS=0`: disable all-voting genesis preseed mode.
- `SOLANA_STAKED_VALIDATORS=0`: only the bootstrap validator votes/produces the chain.
- `SOLANA_NON_STAKED_NO_VOTING=1`: non-bootstrap validators skip vote/stake setup and run with `--no-voting`.
- Observer validators restart `agave-validator` if startup fails before RPC peers are discoverable.
- Observer validators keep the default snapshot fetch path. Disabling snapshot
  fetch made the 200-node final health check worse in testing.
- `ACTIVE_COUNT_MODE=process-scan`: health checks count real `agave-validator` processes, not just running containers.
- `BUILD_MODE=nobuild-mount`: generated per-node Dockerfiles are converted to shared-image runtime mounts.

The previous strategy with many dynamically staked non-bootstrap validators can
stall around early slots because the validators do not reliably enter the same
active staked set before consensus needs their votes. Treat that mode as a
separate single-variable consensus experiment, not the main scale test.

## All-Voting Smoke Run

Run this after script changes before committing to a 10-hour run:

```bash
RUN_PREFIX=solana-20-genesis-voting-smoke-$(date +%Y%m%d-%H%M%S) \
VALIDATORS=20 \
STUB_AS_COUNT=20 \
MIN_ACTIVE_SOLANA=20 \
STRICT_VALIDATOR_STATUS=0 \
ACTIVE_COUNT_MODE=process-scan \
ACTIVE_WAIT=1800 \
HEALTH_TIMEOUT=1800 \
STRICT_ACTIVE_SAMPLING=1 \
SLOT_STALL_TIMEOUT=180 \
SLOT_STALL_GRACE=120 \
SLOT_STALL_CHECK_INTERVAL=20 \
DURATION_SECONDS=120 \
TX_RATE=1 \
SKIP_BASE_BUILD=1 \
BUILD_MODE=nobuild-mount \
UP_PRESET=fast \
PYTHON_BIN=/home/bruce/miniconda3/envs/seedpy310/bin/python \
  scripts/run_solana_large_experiment.sh
```

Expected health evidence:

- `final-health-passed.log` has `validator status advisory strict=0`.
- `final-health-passed.log` records advancing slots and block height.
- `block_metrics.csv` records advancing slots and transaction count growth.
- `tx-load/tx_sender.csv` shows sent transactions.

Validated on 2026-06-17 with
`solana-20-genesis-voting-full-ledger-smoke-codex1`: health passed, slot delta
387, transaction count delta 7895, 20/20 voting validators, 0 delinquent
validators, tx sender 159 sent and 0 failed, and containers were cleaned up at
the end.

## 200 All-Voting Staged Smoke

This is the current large all-voting validation target. It is intentionally
slower than the legacy observer run because every validator is a voting
participant, not a no-vote process.

```bash
RUN_PREFIX=solana-200-genesis-staged-anchor-slow-30m-$(date +%Y%m%d-%H%M%S) \
VALIDATORS=200 \
STUB_AS_COUNT=64 \
SOLANA_GENESIS_STAGED_STARTUP=1 \
SOLANA_GENESIS_CORE_VALIDATORS=1 \
SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS=100000000000 \
SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS=1000000000000 \
SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS=50000000 \
MIN_ACTIVE_SOLANA=200 \
STRICT_VALIDATOR_STATUS=0 \
ACTIVE_COUNT_MODE=process-scan \
ACTIVE_INTERVAL=30 \
ACTIVE_WAIT=7200 \
HEALTH_TIMEOUT=7200 \
CORE_HEALTH_TIMEOUT=3600 \
CORE_ACTIVE_WAIT=1800 \
CORE_SLOT_RETRIES=10 \
CORE_STABILIZE_SECONDS=300 \
CORE_STABILIZE_CHECK_INTERVAL=30 \
STRICT_ACTIVE_SAMPLING=1 \
SLOT_RETRIES=10 \
SLOT_STALL_TIMEOUT=600 \
SLOT_STALL_GRACE=300 \
SLOT_STALL_CHECK_INTERVAL=60 \
DURATION_SECONDS=1800 \
TX_RATE=1 \
REQUIRE_TX_HEALTH=0 \
TX_SUBMIT_TIMEOUT=90 \
SOLANA_METRICS_RPC_TIMEOUT=90 \
SOLANA_SLOTS_PER_EPOCH=262144 \
SOLANA_DISABLE_WARMUP_EPOCHS=1 \
SOLANA_OBSERVER_BATCH_SIZE=10 \
SOLANA_OBSERVER_JOBS=1 \
SOLANA_OBSERVER_BATCH_DELAY=30 \
SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT=4 \
UP_PHASE_DELAY=20 \
WAIT_FOR_LOAD_BELOW=100 \
LOAD_WAIT_TIMEOUT=7200 \
LOAD_WAIT_INTERVAL=60 \
SKIP_BASE_BUILD=1 \
BUILD_MODE=nobuild-mount \
UP_PRESET=fast \
PYTHON_BIN=/home/bruce/miniconda3/envs/seedpy310/bin/python \
  scripts/launch_solana_detached.sh
```

Known 2026-06-18 result:

- `solana-200-genesis-staged-anchor-core1-30m-20260618-211210` proved the
  AS2/IX100 anchor fixed the earlier high-index AS entrypoint reachability
  problem: all observer batches started.
- The same run did not prove 200 voting validators stable. Final health active
  process count plateaued around 191 and the host load average exceeded 10000;
  the run was manually stopped to recover the machine.
- Treat `*-health-running.log` as in-progress evidence only. A valid run must
  produce `final-health-passed.log` after the health command returns
  successfully.

2026-06-21 follow-up:

- `solana-limit-lm25-codex-20260621-2305-v190` reached 190/190 active
  `agave-validator` processes and slots advanced during final health
  (`4719 -> 4775`), but final health failed because the one-shot startup
  transfer did not submit within 180 seconds.
- That failure is not evidence that consensus stopped. Bootstrap logs were
  still advancing slots and transaction count was readable, but some RPC calls
  such as `block-height` timed out under load.
- For large limit runs, use `REQUIRE_TX_HEALTH=0` and keep `TX_RATE > 0`.
  The final summary remains strict: it fails unless `tx-load/tx_sender.csv`
  records sent transactions and `block_metrics.csv` shows transaction-count
  growth.
- `send_solana_tx.py` wraps each transfer attempt with `TX_SUBMIT_TIMEOUT`, so
  a slow RPC call records a failed attempt and the sender keeps retrying
  instead of hanging for the rest of the experiment.

## 300 All-Voting 10-Hour Run

This is the direct 300-validator all-voting run. It is stricter and heavier
than the legacy 200-container observer/process baseline.

Do not run this until the 200-validator staged smoke above passes. Use the same
staged strategy and increase scale gradually.

Apply host network tuning first:

```bash
sudo scripts/solana_host_tuning.sh apply
```

```bash
RUN_PREFIX=solana-300-genesis-voting-10h-$(date +%Y%m%d-%H%M%S) \
VALIDATORS=300 \
STUB_AS_COUNT=96 \
SOLANA_GENESIS_STAGED_STARTUP=1 \
SOLANA_GENESIS_CORE_VALIDATORS=1 \
MIN_ACTIVE_SOLANA=300 \
STRICT_VALIDATOR_STATUS=0 \
ACTIVE_COUNT_MODE=process-scan \
ACTIVE_INTERVAL=30 \
ACTIVE_WAIT=7200 \
HEALTH_TIMEOUT=7200 \
CORE_HEALTH_TIMEOUT=3600 \
CORE_ACTIVE_WAIT=1800 \
CORE_SLOT_RETRIES=10 \
CORE_STABILIZE_SECONDS=300 \
CORE_STABILIZE_CHECK_INTERVAL=30 \
STRICT_ACTIVE_SAMPLING=1 \
SLOT_RETRIES=10 \
SLOT_STALL_TIMEOUT=600 \
SLOT_STALL_GRACE=300 \
SLOT_STALL_CHECK_INTERVAL=60 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
REQUIRE_TX_HEALTH=0 \
TX_SUBMIT_TIMEOUT=90 \
SOLANA_METRICS_RPC_TIMEOUT=90 \
SOLANA_SLOTS_PER_EPOCH=262144 \
SOLANA_DISABLE_WARMUP_EPOCHS=1 \
SOLANA_OBSERVER_BATCH_SIZE=10 \
SOLANA_OBSERVER_JOBS=1 \
SOLANA_OBSERVER_BATCH_DELAY=30 \
SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT=4 \
UP_PHASE_DELAY=20 \
WAIT_FOR_LOAD_BELOW=100 \
LOAD_WAIT_TIMEOUT=7200 \
LOAD_WAIT_INTERVAL=60 \
SKIP_BASE_BUILD=1 \
BUILD_MODE=nobuild-mount \
UP_PRESET=fast \
PYTHON_BIN=/home/bruce/miniconda3/envs/seedpy310/bin/python \
  scripts/launch_solana_detached.sh
```

If this fails, use the same command with `VALIDATORS=200`, then increase in
steps such as `225`, `250`, `275`, `300`. Keep
the default `SOLANA_GENESIS_VOTING_VALIDATORS=1`; do not switch back to observer
mode for this voting-validator question.

For 10-hour runs, use `scripts/launch_solana_detached.sh` instead of invoking
`scripts/run_solana_large_experiment.sh` directly. The detached launcher writes
`launcher.pid`, `launcher.log`, and `run.log` under
`experiments/$RUN_PREFIX/` and prevents the terminal or Codex session from
terminating the experiment before summary generation.

Check a detached run with:

```bash
RUN=solana-300-genesis-voting-10h-YYYYMMDD-HHMMSS
tail -f experiments/$RUN/run.log
cat experiments/$RUN/solana-large/experiment_summary.txt
```

## Legacy Validated 200 Observer Command

This is a legacy strict 10-hour observer/process-scale run. It is useful only
when measuring how many `agave-validator` processes can stay alive. It is not
evidence that all validators participate in consensus. It uses the 200-validator
configuration validated by `solana-200-bootstraponly-30m-strict-20260616-182550`:
fast observer startup, followed by a 2400-second post-health settle so transient
observer restarts happen before sampling starts.

```bash
RUN_PREFIX=solana-200-bootstraponly-10h-$(date +%Y%m%d-%H%M%S) \
VALIDATORS=200 \
STUB_AS_COUNT=64 \
SOLANA_STAKED_VALIDATORS=0 \
SOLANA_NON_STAKED_NO_VOTING=1 \
SOLANA_SPLIT_VALIDATOR_PHASES=1 \
SOLANA_TWO_STAGE_STARTUP=1 \
SOLANA_OBSERVER_BATCH_SIZE=20 \
SOLANA_OBSERVER_JOBS=2 \
SOLANA_OBSERVER_BATCH_DELAY=20 \
SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT=8 \
UP_PHASE_DELAY=60 \
CORE_ACTIVE_WAIT=1200 \
CORE_STABILIZE_SECONDS=180 \
CORE_STABILIZE_CHECK_INTERVAL=30 \
MIN_ACTIVE_SOLANA=200 \
ACTIVE_COUNT_MODE=process-scan \
ACTIVE_INTERVAL=30 \
ACTIVE_WAIT=3600 \
ACTIVE_STALL_TIMEOUT=180 \
ACTIVE_STALL_GRACE=300 \
POST_HEALTH_STABILIZE_SECONDS=2400 \
STRICT_ACTIVE_SAMPLING=1 \
SLOT_RETRIES=5 \
SLOT_STALL_TIMEOUT=600 \
SLOT_STALL_GRACE=300 \
SLOT_STALL_CHECK_INTERVAL=60 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
SKIP_BASE_BUILD=1 \
UP_PRESET=fast \
PYTHON_BIN=/home/bruce/miniconda3/envs/seedpy310/bin/python \
  scripts/run_solana_large_experiment.sh
```

Validated 30-minute strict observer/process-scale result:

- Run: `solana-200-bootstraponly-30m-strict-20260616-182550`.
- Summary: `status=pass`.
- Active `agave-validator` processes: 37 samples, min/max/last all `200`.
- Blocks/transactions: `slot_delta=4859`, `tx_count_delta=6852`,
  `tx_sender_sent=1997`, `tx_sender_failed=0`.
- Cleanup: `docker compose down -v --remove-orphans` completed automatically.

The earlier run `solana-200-bootstraponly-30m-active-20260616-fix2` used only a
600-second settle and completed, but `active_validators.csv` dipped to 190
early in sampling. Keep `POST_HEALTH_STABILIZE_SECONDS=2400` for strict
`MIN_ACTIVE_SOLANA=200` experiments.

## Legacy Observer Fast Smoke Run

Use this before a long run after changing scripts:

```bash
RUN_PREFIX=solana-smoke-20-$(date +%Y%m%d-%H%M%S) \
VALIDATORS=20 \
STUB_AS_COUNT=10 \
MIN_ACTIVE_SOLANA=20 \
DURATION_SECONDS=900 \
TX_RATE=1 \
SKIP_BASE_BUILD=1 \
PYTHON_BIN=/home/bruce/miniconda3/envs/seedpy310/bin/python \
  scripts/run_solana_large_experiment.sh
```

## Legacy 300 Observer Exploration

This is the direct 300-container observer scale-up of the validated 200-container
configuration. It is intentionally strict and may fail if the machine cannot
hold 300 live `agave-validator` processes.

```bash
RUN_PREFIX=solana-300-bootstraponly-10h-$(date +%Y%m%d-%H%M%S) \
VALIDATORS=300 \
STUB_AS_COUNT=96 \
SOLANA_STAKED_VALIDATORS=0 \
SOLANA_NON_STAKED_NO_VOTING=1 \
SOLANA_SPLIT_VALIDATOR_PHASES=1 \
SOLANA_TWO_STAGE_STARTUP=1 \
SOLANA_OBSERVER_BATCH_SIZE=20 \
SOLANA_OBSERVER_JOBS=2 \
SOLANA_OBSERVER_BATCH_DELAY=30 \
SOLANA_OBSERVER_COMPOSE_PARALLEL_LIMIT=8 \
UP_PHASE_DELAY=60 \
CORE_ACTIVE_WAIT=1200 \
CORE_STABILIZE_SECONDS=180 \
CORE_STABILIZE_CHECK_INTERVAL=30 \
MIN_ACTIVE_SOLANA=285 \
ACTIVE_COUNT_MODE=process-scan \
ACTIVE_INTERVAL=30 \
ACTIVE_WAIT=5400 \
ACTIVE_STALL_TIMEOUT=300 \
ACTIVE_STALL_GRACE=300 \
POST_HEALTH_STABILIZE_SECONDS=600 \
SLOT_RETRIES=5 \
SLOT_STALL_TIMEOUT=600 \
SLOT_STALL_GRACE=300 \
SLOT_STALL_CHECK_INTERVAL=60 \
DURATION_SECONDS=36000 \
TX_RATE=1 \
SKIP_BASE_BUILD=1 \
UP_PRESET=fast \
PYTHON_BIN=/home/bruce/miniconda3/envs/seedpy310/bin/python \
  scripts/run_solana_large_experiment.sh
```

If this fails, first lower `VALIDATORS` to `250`, then `225`, then `200`. Do not
switch back to dynamic staking while you are measuring the all-voting scale
limit.

## Result Files

The result directory is:

```bash
experiments/$RUN_PREFIX/solana-large
```

Important files:

- `final-health-passed.log`: final liveness check, including active process
  count, voting validator count, and delinquent validator count.
- `active_validators.csv`: running Solana containers and live `agave-validator`
  process count during the sampling window.
- `core-stabilize.log`: legacy two-stage mode only; slot progression before
  observer validators are attached.
- `block_metrics.csv`: slot, block height, transaction count, voting validator
  samples, and delinquent validator samples.
- `memory_raw.csv`: per-container memory samples.
- `memory_by_type.csv`: grouped memory samples.
- `memory_latest_by_type.csv`: last grouped memory sample.
- `tx-load/tx_sender.csv`: continuous transaction send attempts.
- `experiment_summary.txt`: pass/fail summary for active validators, slots,
  transactions, and memory.
- `experiment_summary.json`: machine-readable version of the same summary.
- `solana-container-logs/*.top`: final process snapshots for each Solana container.
- `up-logs/summary.log`: batched startup trace.

The script runs `docker compose down -v --remove-orphans` automatically unless
`KEEP_LAST=1` is set.

## Useful Overrides

- `VALIDATORS=200`: total Solana validators including bootstrap.
- `STUB_AS_COUNT=64`: number of service stub ASes.
- `DURATION_SECONDS=36000`: sampling duration after final health passes.
- `TX_RATE=1`: continuous transfer rate, one transaction per second.
- `MIN_ACTIVE_SOLANA=200`: required real `agave-validator` process count.
- `ACTIVE_COUNT_MODE=process-scan`: fast host-side process counting.
- `ACTIVE_INTERVAL=30`: active-process sampling interval in seconds.
- `SLOT_STALL_TIMEOUT=600`: fail if slot stops advancing during sampling.
- `ACTIVE_STALL_TIMEOUT=180`: fail if live validator count remains below
  `MIN_ACTIVE_SOLANA` for this many seconds after the grace period.
- `POST_HEALTH_STABILIZE_SECONDS=2400`: wait after final health, then rerun
  final health before sampling starts.
- `STRICT_ACTIVE_SAMPLING=1`: fail the experiment summary if any sampling
  point has fewer than `MIN_ACTIVE_SOLANA` live `agave-validator` processes.
- `SOLANA_LOG_TAIL=2000`: per-container log lines captured at the end or on failure.
- `SOLANA_STAKED_VALIDATORS=0`: legacy mode only; keep non-bootstrap validators out of consensus.
- `SOLANA_NON_STAKED_NO_VOTING=1`: legacy mode only; make non-staked validators skip vote setup and run `--no-voting`.
- `SOLANA_GENESIS_VOTING_VALIDATORS=1`: all validators are placed into genesis
  and start as voting validators; this overrides observer/dynamic-stake mode.
- `SOLANA_GENESIS_BOOTSTRAP_STAKE_LAMPORTS`: defaults to `100000000000`.
- `SOLANA_GENESIS_CORE_VALIDATORS`: defaults to `31`; these are the first
  non-bootstrap validators in deterministic validator index order.
- `SOLANA_GENESIS_CORE_VALIDATOR_STAKE_LAMPORTS`: defaults to `1000000000000`.
- `SOLANA_GENESIS_VALIDATOR_STAKE_LAMPORTS`: defaults to `50000000` for
  non-core non-bootstrap validators. They still start as voting validators.
- `STRICT_VALIDATOR_STATUS=0`: default for large genesis-voting experiments;
  current/delinquent validator counts are logged as advisory data.
- `STRICT_VALIDATOR_STATUS=1`: make validator status a hard gate. Use with
  `MIN_VOTING_VALIDATORS=N` and `MAX_DELINQUENT_VALIDATORS=0` only for smaller
  or deliberately strict experiments where delayed current/delinquent status is
  acceptable as a blocker.
- `SOLANA_OBSERVER_BATCH_SIZE=20`: legacy observer startup batch size.
- `SOLANA_OBSERVER_JOBS=2`: legacy observer batches launched concurrently.
- `SOLANA_OBSERVER_BATCH_DELAY=20`: legacy delay between observer batch launches.
- `BUILD_MODE=nobuild-mount`: avoid per-node Docker builds.
- `SKIP_BASE_BUILD=1`: skip rebuilding the shared `seedemu-solana` image.
- `KEEP_LAST=1`: keep containers running after a successful experiment.
