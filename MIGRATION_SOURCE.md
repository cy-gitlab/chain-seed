# Extraction Source

## SeedEmu snapshot

- Repository: `/home/bruce/seed-emulator`
- Branch: `development`
- Base commit: `7d776fd326d77dfd54d82f5f2b9a6c18d80a82c0`
- Description at extraction: `v1.2-322-g7d776fd3-dirty`
- Extraction date: `2026-07-27`
- Source mode: current working tree, copied without modifying the source

The working tree contained ongoing blockchain work. ChainSeed therefore uses
the remote Development snapshot plus the local Monero and Solana overrides,
the local Lighthouse fork, and the preserved D61 large-scale definitions.

## Copied ownership

| SeedEmu source | ChainSeed destination |
|---|---|
| `seedemu/services/EthereumService` | `chainseed/ethereum` |
| `seedemu/services/MoneroService` | `chainseed/monero` |
| `seedemu/services/SolanaService` | `chainseed/solana` |
| `seedemu/services/ChainlinkService` | `chainseed/chainlink` |
| `seedemu/testing/blockchain.py` | `chainseed/testing/blockchain.py` |
| `docker_images/seedemu-*` blockchain images | `images/` |
| `tools/Blockchain` | `tools/blockchain` |
| `examples/blockchain` canonical examples | `examples/` |
| `D61_large_scale` reproducible inputs | `experiments/large_scale` |
| Blockchain tests | `tests/protocol` |

## Excluded generated state

- `output/` compiled deployments
- `experiments/` raw benchmark runs inside D61
- `datasets/` downloaded Xatu data
- `multiday_models/` generated topology model outputs
- `perf-data/`, client databases, logs, packet captures, and temporary state
- `node_modules/`, `__pycache__/`, `*.pyc`, and test caches

Large datasets and benchmark outputs must be published separately with URI,
checksum, schema version, and generator commit.

## Known compatibility debt

The coordinated SeedEmu branch now exposes the compiler-neutral
`Node.setBaseImage(ImageRef)` contract and generic Docker Compose service
registration used by ChainSeed. ChainSeed no longer uses Docker's private image
override implementation, and EthExplorer deployment is now contributed by
`chainseed.ethereum.EthExplorer`. The original SeedEmu blockchain services
and Explorer code remain temporarily available for comparison and will be
removed only after the extracted path passes the agreed compatibility gate.
