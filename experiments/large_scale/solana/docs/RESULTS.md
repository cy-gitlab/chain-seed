# Solana Large-Scale Results

This file records the empirical limit reached by the current D61 Solana
all-voting, genesis-preseeded, low-memory profile. The pass criterion is a
strict 10-hour sampling run: all expected validator processes must stay active,
slots must advance, transactions must be sent and recorded, memory must be
sampled, and the sampled duration must cover the required 35400 seconds.

## Current Empirical Limit

The highest scale currently proven by a 10-hour run is **201 validators**.

`202` validators is not proven. It failed strict checks before reaching the
10-hour duration because the active validator count dropped from `202` to `201`.
The tightest proven boundary for the current machine and profile is therefore:

- Proven pass: `201` validators.
- First adjacent failed level: `202` validators.

## Evidence

| Validators | Status | Active Min/Last | Slot Delta | Tx Count Delta | Max Elapsed | Required Elapsed | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 200 | pass | 200 / 200 | 7188 | 1437738 | 36331.959s | 35400.0s | `experiments/solana-limit-lm0-procscan-confirm-20260625-sweep/summary.csv` |
| 201 | pass | 201 / 201 | 7136 | 1423071 | 36599.076s | 35400.0s | `experiments/solana-limit-lm0-procscan-201retry-20260627-0755-sweep/summary.csv` |
| 202 | fail | 201 / 201 | 5705 | 1139548 | 29875.143s | 35400.0s | `experiments/solana-limit-lm0-procscan-after201-20260627-sweep/summary.csv` |
| 205 | fail | 204 / 204 | 2513 | 473322 | 13888.862s | 35400.0s | `experiments/solana-limit-lm0-procscan-confirm-20260625-sweep/summary.csv` |

## 202-Validator Failure Details

The `202` run continued producing slots and transactions, but it did not meet
the all-validator and duration criteria:

- `active-stall-detected.log` recorded `active_agave=201`,
  `running_solana=201`, `min_active_solana=202`.
- The first active stall was recorded at `2026-06-28T05:09:26+08:00`.
- `experiment_summary.txt` reported `active_min=201`, `active_last=201`,
  `below_min_samples=5`.
- `duration_max_elapsed=29875.143`, below the required `35400.0`.
- `host_mem_available_min=1.57 GiB` and `host_swap_free_min=0.00 GiB`, so the
  failure happened under very low host memory headroom.
- `slot_delta=5705` and `tx_count_delta=1139548`, so the chain itself did not
  fail by becoming idle or empty.

The queued `after202` sweep did not start `203+` validators because it first
validated the existing `202` run and stopped on that strict failure.

## Interpretation

Under the current configuration and host resource state, the experimentally
supported scale is **201 validators for 10 hours**. Higher levels need a new
profile or more host resources before they can be considered proven.

