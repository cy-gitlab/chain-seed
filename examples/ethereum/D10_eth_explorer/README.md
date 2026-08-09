# D10 EthExplorer Example

This example runs an Ethereum PoS private network and attaches the ChainSeed
EthExplorer stack. The explorer integration is owned by
`chainseed.ethereum.EthExplorer`; its image recipes and runtime assets are
owned by [`images/ethexplorer`](../../../images/ethexplorer).

The explorer images are adapted from
[eth2-beaconchain-explorer](https://github.com/gobitfly/eth2-beaconchain-explorer)
and [beaconchain](https://github.com/gobitfly/beaconchain).

## What the example creates

The example reuses D01 to create:

- three Geth execution nodes;
- three Lighthouse beacon nodes;
- nine genesis validator clients;
- one beacon setup node;
- Faucet and utility services;
- the Internet Map;
- the EthExplorer web, backend, and data services.

EthExplorer selects one rendered `PoSGethServer` and one rendered
`PoSBeaconServer` from the requested Ethereum chain. It uses the service
types and chain name rather than container display-name patterns.

## Compile and run

Run from the ChainSeed repository root:

```bash
chainseed-test compile examples/ethereum/D10_eth_explorer/example.yaml
chainseed-test all examples/ethereum/D10_eth_explorer/example.yaml
```

To compile the example directly:

```bash
python examples/ethereum/D10_eth_explorer/ethereum_pos.py \
  --platform amd \
  --output examples/ethereum/D10_eth_explorer/output
```

Then start or stop the generated deployment:

```bash
cd examples/ethereum/D10_eth_explorer/output
docker compose up -d --build
docker compose down --remove-orphans
```

AMD64 is the default. Use `--platform arm` for ARM64.

## Python integration

D01 renders the emulator first, then gives the rendered topology and Docker
compiler to the ChainSeed component:

```python
emu.render()

docker = ChainSeedDocker(internetMapEnabled=True, platform=platform)
docker.enableEthExplorer(chain_name="pos", port=5000)

emu.compile(docker, output, override=True)
```

The `--eth-explorer` and `--no-eth-explorer` command-line options control
this step. The older `--ether-view` names remain accepted as aliases during
the migration.

SeedEmu calls a protected additional-services hook while compiling Compose.
`ChainSeedDocker` overrides the hook, obtains service definitions from
`EthExplorer`, and resolves the selected emulated network names through its
inherited compiler helper. SeedEmu does not know which containers belong to
EthExplorer.

## Services and networking

| Service | Purpose |
| --- | --- |
| `seedemu-ethexplorer-web` | Web UI, search, validator tagging, and frontend updates |
| `seedemu-ethexplorer-backend` | Execution and consensus indexing, statistics, and schema initialization |
| `postgres` / `alloy` | Explorer and indexer relational data |
| `clickhouse` | Analytical chain data |
| `redis` | Cache and session data |
| `littlebigtable` / `rawbigtable` | Local Bigtable-compatible storage |

The data services share the Compose default network. The web and backend
services also join the two emulated networks containing the selected Geth and
Beacon nodes. Their addresses are provided as:

- `EL_HOST`: selected Geth node address;
- `CL_HOST`: selected Beacon node address.

The web UI is published at `http://127.0.0.1:5000`. The Internet Map remains
available at `http://127.0.0.1:8080/map.html`.

## Image layout

```text
images/ethexplorer/
├── docker-compose.yml
├── beaconchain/backend/
│   ├── Dockerfile
│   ├── start.sh
│   └── local_deployment/
└── eth2-beaconchain-explorer/
    ├── Dockerfile
    ├── start.sh
    └── local-deployment/
```

The default image references are declared by
`chainseed.ethereum.EthExplorer`:

```text
handsonsecurity/seedemu-ethexplorer-backend:1.0
handsonsecurity/seedemu-ethexplorer-web:1.0
```

They can be overridden through `ChainSeedDocker.enableEthExplorer()`.

## Validation

The manifest-driven test checks:

- the expected Geth, Beacon, and validator topology;
- that Explorer services carry the ChainSeed ownership label;
- container readiness and Explorer HTTP availability;
- consensus slot and epoch indexing;
- a signed execution-layer transaction and its Explorer pages.

## Troubleshooting

If `http://127.0.0.1:5000` is unavailable, inspect service state and logs:

```bash
docker compose ps
docker compose logs -f seedemu-ethexplorer-backend
docker compose logs -f seedemu-ethexplorer-web
```

Initial indexing can take several slots while Geth, Lighthouse, Postgres,
ClickHouse, Redis, and Bigtable-compatible services become ready.

If port 5000 is occupied, change the component configuration and recompile:

```python
docker.enableEthExplorer(chain_name="pos", port=5050)
```
