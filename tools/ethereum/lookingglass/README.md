# Ethereum Lookingglass

View Ethereum nodes grouped by AS, client dependencies, resources, block-head
observations and transaction receipts.

## Include in a deployment

Enable Lookingglass in the same place as EthExplorer, before compilation:

```python
from chainseed import ChainSeedDocker

docker = ChainSeedDocker()
enable_lookingglass = True
if enable_lookingglass:
    docker.enableLookingglass("pos", port=8765)
emu.compile(docker, "output")
```

Replace `"pos"` with your chain name. Without the call, no Lookingglass service
is generated. Build and start the generated Compose deployment as usual;
Lookingglass starts and stops with it. Open http://127.0.0.1:8765.
The generated `output/lookingglass/` contains its complete build context.
The first build downloads a Python base image and installs PyYAML.

The component queries heads, peers, Metrics and txpool directly. It connects to
the selected chain's local networks and needs no Docker socket or privileged mode.
APIs unavailable on a node appear as unknown/errors. Disable queries with
`probe_peers=False` or adjust `peer_interval`, `peer_jobs` and `metrics_interval`.

Optionally mount a measurement directory:

```python
docker.enableLookingglass("pos", run_dir="results/run-1",
                         explorer_url="http://localhost:5000")
```

The directory is created at compilation if needed and mounted read-only.
Run collectors separately to populate resource, consensus and transaction views;
these fields remain unknown until observations are available. The mount resolves
against the working directory when the component is created. Queries add load,
so keep the same settings across compared measurements.

## Standalone viewer

Run the following commands from the repository root.

Start data collection as described in the [D61 manual](../../../examples/D61_large_scale/ethereum/README.md), then run:

```bash
python tools/ethereum/lookingglass/server.py \
  --run-dir examples/D61_large_scale/ethereum/results/run-1
```

Open http://127.0.0.1:8765. Press Ctrl-C to stop the viewer.
The same command can view a completed measurement.

The server reads existing files and does not start containers or collectors.
With only the installed package, use `python -m chainseed.ethereum.lookingglass.server`
with the same arguments. Runtime code and web assets are included in the wheel.

## Optional node queries

```bash
python tools/ethereum/lookingglass/server.py \
  --run-dir examples/D61_large_scale/ethereum/results/run-1 \
  --probe-peers --explorer-url http://localhost:5000
```

This also queries Beacon/Geth peers, Lighthouse Metrics and Geth txpool status.
The host must be able to reach the nodes' private IPs. The tool does not enable
client APIs; unavailable APIs appear as unknown or errors.

In standalone file mode, probes pause when the resource collector stops or its status/resource samples
are older than 180 seconds. Keep the viewer attached to the current deployment;
stop it before recreating containers or reusing node IPs. Queries add load:
`--peer-interval`, `--peer-jobs` and `--metrics-interval` control frequency
and concurrency. Omit `--probe-peers` for passive viewing during measurements.

## Data

| View | Input relative to the results directory |
| --- | --- |
| Nodes and configuration | `observed-compose.yml`, `deployment.json` |
| Memory, CPU and client dependencies | `memory_latest_raw.csv` |
| Head slots and block observations | `consensus/beacon_head_samples.csv`, `consensus/block_first_seen.csv` |
| Sent transactions | `tx-load/tx_sender.csv` |
| Included transactions | `tx_receipts.csv` |
| P2P links, processor queues and txpool | Optional node queries |

Use `--compose PATH` before a Compose snapshot is available, or
`--tx-dir DIR` and `--receipts PATH` for separately stored transaction data.
Missing samples remain unknown. Resource trends contain samples observed since
the viewer started; large CSVs use bounded recent tails.

File-based block timestamps come from polling rounds; live component queries
record their API response times. Neither measures exact arrival order or message
propagation paths. File views include only the collector's sampled Beacon nodes.
An included transaction with no execution status is marked
unknown. Queue values are interval means; txpool totals count copies across nodes.

Select an AS to expand it, then select nodes to inspect or compare them.
The page supports block/transaction search, observation replay, JSON export and SVG export.

## Demo and remote access

Preview the interface without an emulation:

```bash
python tools/ethereum/lookingglass/server.py --demo
```

Demo data is labeled synthetic. It is not an experiment result.

For remote access, use SSH forwarding:

```bash
ssh -L 8765:127.0.0.1:8765 USER@HOST
```

Alternatively, `--host HOST_IPV4 --port 8765` binds a host interface.
There is no authentication; clients reaching that port can read the displayed data.
