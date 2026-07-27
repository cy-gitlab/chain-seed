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
| `seedemu/services/EthereumService` | `src/chainseed/ethereum` |
| `seedemu/services/MoneroService` | `src/chainseed/monero` |
| `seedemu/services/SolanaService` | `src/chainseed/solana` |
| `seedemu/services/ChainlinkService` | `src/chainseed/chainlink` |
| `seedemu/testing/blockchain.py` | `src/chainseed/testing/blockchain.py` |
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

The current SeedEmu release does not yet expose the compiler-neutral
`Node.setBaseImage(ImageRef)` contract described in `docs/design`. The copied
Monero custom-image feature therefore temporarily retains its existing Docker
compiler integration. This exception is isolated and should be removed after
SeedEmu publishes the extension API.
