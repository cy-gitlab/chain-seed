# Worker Side-Effect Experiments

## 1. Current conclusion

Reducing the Lighthouse Beacon Processor worker cap is the only tested fork
component that materially reduces Beacon memory. A worker is an internal
processing slot: fewer slots reduce per-node scheduling and temporary work
state, but may cause tasks to wait when several kinds of work arrive together.

A degradation is attributed to the worker cap only when increasing load causes
worker cap 2 to degrade earlier or faster than worker caps 4 and 8. If every cap
degrades similarly, the bottleneck belongs to another part of the emulator.

The completed API-contention experiment used 300 Beacon nodes, 300 Geth nodes,
900 validators, and 1 transaction/second. Thirty Beacon nodes received up to
120 read requests/second in total. All worker caps completed with:

```text
100% API success
100% steady-state validator liveness
2-epoch steady-state finality lag
100% transaction submission and commitment
no missed slots or reorgs
```

Therefore ordinary observer API traffic is not currently a demonstrated worker
side effect. The same run produced a more important signal: with the old
polling collector, 20.0% to 32.7% of blocks under worker cap 2 were not visible
on every sampled Beacon until the next approximately 2.6-second polling round.
The corresponding ranges were 2.7% to 10.0% for cap 4 and 0% to 4.7% for cap 8.
This is evidence of delayed local block processing, but the polling resolution
is too coarse to treat 2.6 seconds as the real delay.

## 2. Experiment order

The remaining worker-side-effect experiments are ordered as follows:

1. Completed: high-resolution block visibility under 1 TPS found no worker-cap penalty.
2. Current: increase mixed-transaction load and resulting block fullness; see `RUNBOOK_WORKER_TX_STRESS.md`.
3. Slow selected Geth nodes and measure whether low worker caps amplify delays.
4. Increase achieved Beacon peer counts, then repeat with the Xatu-derived P2P topology.
5. Create short valid-message bursts and compare recovery time.
6. Test heavier historical/state API queries only if observer robustness remains relevant.

Long partitions, long offline recovery, and invalid-message floods are deferred.
They are less representative of the emulator's normal use and require more
specialized fault injection.

## 3. High-resolution block-visibility experiment

This is the first experiment to run. Every formal configuration is sampled for
ten hours and uses:

```text
300 Beacon nodes
300 Geth nodes
900 Validator clients and active validators
64 stub ASes
1 transaction/second
EtherView disabled
worker caps 2, 4, and 8, run serially
```

The only causal variable is the worker cap. The existing memory, transaction,
attestation, liveness, finality, and reorg collectors remain enabled.

The new collector opens persistent Beacon event streams to 64 evenly distributed
Beacon nodes. A node pushes a head event immediately after it processes a new
canonical head. All arrivals are timestamped by one host process using the same
monotonic clock, avoiding cross-container clock differences and repeated
`docker exec` calls.

For each slot, the summarizer selects the block root observed by the most sampled
nodes. Blocks seen by fewer than 95% of sampled nodes are retained in the raw
data but excluded from propagation percentiles. The first five minutes and last
24 seconds are excluded to remove connection startup and incomplete tail slots.

The primary metric is:

```text
time from the first observation until 95% of sampled nodes observe the same block
```

The output also reports the percentage of blocks requiring more than 0.5, 1,
and 2 seconds; full first-to-last spread; per-node late-observation rate; memory;
CPU; attestation inclusion; liveness; finality; transaction commitment; missed
slots; and reorgs.

## 4. Build and verify

Run from:

```bash
cd ~/chain-seed/experiments/large_scale/ethereum
```

Build only if the Lighthouse patch or base image changed:

```bash
scripts/build_lighthouse_component_fork.sh
```

Verify the reusable image:

```bash
scripts/verify_lighthouse_component_image.sh \
  handsonsecurity/seedemu-ethereum:pos2.0-baseline-components
```

## 5. Short infrastructure check

This validates event-stream discovery, collection, summarization, and cleanup.
It is not paper data:

```bash
SWEEP_ID="$(date +%Y%m%d-%H%M%S)-worker-block-smoke" \
BEACONS=20 \
STUB_AS_COUNT=10 \
BLOCK_EVENT_BEACON_SAMPLE=8 \
DURATION_SECONDS=900 \
  scripts/run_worker_block_visibility_sweep.sh --worker-caps '2'
```

## 6. Run the 300-Beacon experiment manually

Use one experiment ID for all caps:

```bash
export SWEEP_ID="$(date +%Y%m%d-%H%M%S)-worker-block-visibility-300"
```

Run cap 2 for ten hours and inspect it before continuing:

```bash
DURATION_SECONDS=36000 \
  scripts/run_worker_block_visibility_sweep.sh --worker-caps '2'
```

Then run caps 4 and 8 with the same `SWEEP_ID`:

```bash
DURATION_SECONDS=36000 \
  scripts/run_worker_block_visibility_sweep.sh --worker-caps '4'
DURATION_SECONDS=36000 \
  scripts/run_worker_block_visibility_sweep.sh --worker-caps '8'
```

To run all configurations serially without stopping between successful runs:

```bash
DURATION_SECONDS=36000 \
  scripts/run_worker_block_visibility_sweep.sh --worker-caps '2 4 8'
```

The default duration is also 36,000 seconds, but the formal commands specify it
explicitly. Different caps must not run concurrently.

## 7. Output

Sweep-level results:

```text
experiments/worker-block-visibility/<SWEEP_ID>/runs.csv
experiments/worker-block-visibility/<SWEEP_ID>/summary.csv
experiments/worker-block-visibility/<SWEEP_ID>/sweep.log
```

Per-run high-resolution data:

```text
block-visibility/head_events.csv
block-visibility/connections.csv
block-visibility/sampled_beacons.csv
block-visibility/run_info.json
block-visibility/block_visibility.csv
block-visibility/summary.csv
block-visibility/summary.json
```

Raw events are never deleted by the summarizer. The benchmark runner stops the
event collector cleanly and always runs `docker compose down -v
--remove-orphans` on success, failure, or interruption.

## 8. Decision rule

Cap 2 remains acceptable if its high-resolution block visibility is close to
cap 4 and consensus quality remains unchanged. Cap 4 becomes the preferred
configuration if it consistently removes a cap-2 delay knee while preserving
most of the memory reduction.

In the latest API experiment, cap 2 used about 0.03 GiB less average memory per
Beacon than cap 4, or roughly 9 GiB across 300 Beacon nodes. This is the memory
cost against which any propagation improvement from cap 4 should be evaluated.
