# D61 Large-Scale Solana

This directory contains the large-scale Solana/Agave experiment that mirrors the
layout used by `../ethereum`.

## Layout

- `src/solana_large.py`: generates a D61-style topology with multiple IXes,
  tier-1 transit ASes, regional transit ASes, and many stub ASes.
- `scripts/`: build, patch, batch-up, health-check, transaction-load, and
  sampling scripts.
- `experiments/`: timestamped experiment results created by the run script.
- `output/`: generated SEED/Docker output; ignored because it is runtime state.

## Default Scale

The default run creates 300 Solana validator containers:

- 1 bootstrap/genesis validator.
- 299 non-bootstrap validators that are also preloaded into genesis.
- 64 stub ASes by default.
- 10 IXes, 7 tier-1 transit ASes, and 14 regional transit ASes.
- Every service stub AS keeps its generated primary/secondary upstreams and is
  also attached to AS2 at IX100 as an anchor provider path. This preserves a
  complex topology while guaranteeing all validators can reach the bootstrap
  prefix; without this anchor, some high-index stub ASes can miss `10.150.0.0/24`
  through valley-free BGP export and validators wait forever for the entrypoint.

The generated Solana output is patched after compilation:

- Add high `nofile` ulimit to Solana containers.
- Use `--slots-per-epoch` from `SOLANA_SLOTS_PER_EPOCH`.
- Disable Solana warmup epochs by default so the configured slots-per-epoch
  applies from epoch 0. This matters for large all-voting runs because warmup
  epochs still start with short 32/64-slot epochs and can reproduce the early
  epoch-boundary stall even when a large `SOLANA_SLOTS_PER_EPOCH` is requested.
- Preload every validator into genesis by default so all validators start with
  identity, vote, and stake accounts and can vote immediately after the network
  is up.
- Publish the bootstrap-initialized full genesis ledger as a shared
  `genesis-ledger.tar`; non-bootstrap validators unpack this ledger before
  startup. Sharing only `genesis.bin` is not enough for Agave 2.2 and causes
  `InvalidBlock(Incomplete)` while loading bank 0.
- Keep legacy dynamic stake and non-voting observer patches available only for
  explicit comparison runs; they are not the default experiment path.
- In legacy observer mode only, add an `agave-validator` restart loop so early
  RPC peer discovery failures do not leave empty containers behind.
- Convert generated per-node Docker builds into shared-image runtime mounts
  when `BUILD_MODE=nobuild-mount`.
- Generate `experiment_summary.txt` and `experiment_summary.json` so a run can
  be judged from active validator, slot, transaction, and memory evidence.

## Main Entry Point

Use `scripts/run_solana_large_experiment.sh` from this directory. The default
mode is now the true voting-validator experiment:
`SOLANA_GENESIS_VOTING_VALIDATORS=1`.

This mode writes every validator's keypairs into the bootstrap genesis ledger
and patches non-bootstrap validators to skip runtime
airdrop/create-vote/delegate setup. Bootstrap first initializes the genesis
blockstore and publishes a full shared genesis ledger tar; validators unpack
that tar and then start with their own identity/vote/stake keypairs. Bootstrap
plus a deterministic core subset of non-bootstrap validators receive higher
genesis stake, while the remaining validators still vote with lower stake.
Non-bootstrap validators are kept as lightweight private-RPC voting nodes
instead of full RPC/history nodes.
Health checks treat `solana validators` current/delinquent status as advisory
by default in this mode (`STRICT_VALIDATOR_STATUS=0`). At large scale and with
staged startup, that CLI status can lag even while validator processes are
running and slots are advancing. Strict active sampling and a slot-stall
watchdog are enabled by default, so a run is invalid if validators stay alive
but the chain stops producing slots. To make current/delinquent status a hard
gate, explicitly set `STRICT_VALIDATOR_STATUS=1`,
`MIN_VOTING_VALIDATORS=VALIDATORS`, and `MAX_DELINQUENT_VALIDATORS=0`.

The older bootstrap-only consensus plus non-voting observer mode is still
available with `SOLANA_GENESIS_VOTING_VALIDATORS=0`, but it only measures how
many `agave-validator` processes can stay alive. It is not evidence that many
validators are participating in consensus.

The script generates fresh output, starts the network, verifies active validator
processes and slot production, starts continuous transaction load, collects
memory and block metrics, then tears the stack down by default. Large sweep
runs skip the startup one-shot transfer health check by default because a
single overloaded bootstrap RPC call can time out even while slots and vote
transactions are advancing. This does not lower the final pass criteria:
`experiment_summary.txt` still requires the tx sender to record at least one
sent transaction and block metrics to show transaction-count growth.

For 200+ all-voting runs, apply runtime host network tuning first with
`sudo scripts/solana_host_tuning.sh apply`. The default host UDP/socket buffer
limits are too small for this scale and can cause Agave gossip/repair drops even
when CPU and memory are sufficient.

Use `scripts/launch_solana_detached.sh` for 10-hour or longer experiments. It
wraps the main run script with `nohup setsid`, writes a launcher PID/log under
`experiments/$RUN_PREFIX/`, and avoids accidental cleanup when the interactive
session exits.

The current validated observer/process-scale result is
`solana-200-bootstraponly-30m-strict-20260616-182550`: 200 containers, 30 minutes
of sampling, active `agave-validator` process min/max/last all 200, advancing
slots, non-empty transaction load, memory samples, and automatic cleanup. Treat
that result as an observer/process baseline, not as all-voting validator
evidence.

For the current all-voting commands and the legacy observer appendix, see
`docs/RUNBOOK.md`.

For the current empirical 10-hour limit and pass/fail evidence, see
`docs/RESULTS.md`. The current proven all-voting limit is 201 validators; the
adjacent 202-validator run failed strict checks before reaching 10 hours.
