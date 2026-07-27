# ChainSeed

[中文](README.zh-CN.md) | English

ChainSeed provides blockchain protocol extensions for
[SeedEmu](https://github.com/seed-labs/seed-emulator). It owns Ethereum,
Monero, Solana, and Chainlink service layers together with their image recipes,
tools, examples, runtime probes, and large-scale experiment definitions.

The dependency direction is intentionally one way:

```text
chain-seed -> seed-emulator
seed-emulator -X-> chain-seed
```

## Current extraction

This is the first functional extraction from SeedEmu. The copied service
implementations retain their public behavior while using `chainseed.*` package
ownership. No source file was removed from or modified in the source
SeedEmu checkout.

Generated Compose output, client databases, raw benchmark samples, Xatu input
datasets, Python caches, and Node package caches are excluded. The extraction
source and exclusions are recorded in [MIGRATION_SOURCE.md](MIGRATION_SOURCE.md).

## Install for development

```bash
python -m pip install -e /path/to/seed-emulator
python -m pip install -e ".[dev,fidelity]"
```

Use explicit chain imports:

```python
from seedemu import Binding, Emulator, Filter
from chainseed.ethereum import ConsensusMechanism, EthereumService
from chainseed.monero import MoneroService
from chainseed.solana import SolanaService
```

## Layout

```text
src/chainseed/   Python service layers and protocol-aware testing
images/          Blockchain client and explorer image recipes
tools/           Explorer, wallet, and blockchain support tools
examples/        Small canonical examples grouped by protocol
experiments/     Reproducible large-scale definitions without raw output
tests/           ChainSeed imports, protocol behavior, and SeedEmu compatibility
docs/            Architecture and direct-cut migration design
```

The target architecture and remaining SeedEmu API work are documented in
[docs/design](docs/design/README.md).
