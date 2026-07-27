# Ethereum Beacon P2P Static Fidelity Data

This directory reconstructs the topology visible to Xatu observers without
downsampling, filling missing edges, or claiming that the observed ego-networks
are the complete Ethereum mainnet overlay.

The first-stage outputs have the following semantics:

- `G0`: heartbeat edges active in the selected representative snapshot bucket.
- `C`: every edge observed in a heartbeat or connected event during the day.
- `C_new`: `C - G0`.
- Connection events and censored intervals remain available independently from
  the three graph outputs.

The temporal-analysis and model stages add four explicit layers:

- `snapshots`: every observed heartbeat ego-edge in every time bucket, plus
  heartbeat-derived sessions.
- `audit`: observer behavior profiles and the reproducible classification into
  bounded, bursty-probe, and bulk-probe cohorts.
- `fit-model`: an older one-day prototype retained for reproducibility only. It
  is not the approved remote-to-remote topology model.
- `fit-topology`: the earlier single-day propensity fit retained for comparison.
- `prepare-topology-day`: a compact raw-heartbeat preprocessing path that avoids
  materializing duplicate multi-gigabyte normalized observations.
- `fit-topology-multiday`: the current propensity model. It uses chronological
  train, validation, and test days, freezes all parameters before final testing,
  and generates five seeded initial graphs plus their churn snapshots.

The approved generator never treats observer degree as remote-peer degree and
does not prescribe a degree for any generated node. The absolute hidden graph
density remains unidentifiable in Xatu, so it is bounded only by a broad
external range and the client peer safety limit.

Raw Parquet files are immutable and checksum-tracked under `datasets/`, which
is excluded from Git because one Xatu table can be hundreds of megabytes.

See [RUNBOOK.md](RUNBOOK.md) for raw-data commands. The topology model is
documented in [English](model.md) and
[Chinese](model.zh-CN.md).
