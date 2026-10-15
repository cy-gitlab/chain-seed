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

The coordinated SeedEmu `chainseed` branch exposes extension API 1:
compiler-neutral `SystemProfile` objects through `Node.setBaseSystem`, plus
protected Docker hooks for system-image catalogs and additional Compose services.
ChainSeed uses the public `setImageOverride` method for custom Monero images;
EthExplorer deployment is contributed by `chainseed.ethereum.EthExplorer`.
The original SeedEmu blockchain services and Explorer code have been removed
after static ownership, installation and compilation checks. Docker runtime
validation remains pending. File-level provenance is recorded in
`docs/migration-inventory.json`; historical source is retained under
`experiments/legacy`. Four large historical binaries/archives are excluded from
the new repository and recorded with checksums and their original Git revision.
