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

## Relationship and prerequisites

ChainSeed is an add-on package, not a standalone replacement for SeedEmu. A
working SeedEmu checkout and Python environment are prerequisites. Before
adding ChainSeed, users should be able to render, compile, build, and run a
non-blockchain SeedEmu example in that environment.

ChainSeed may import documented public APIs from `seedemu`. In the opposite
direction, SeedEmu must never import or package ChainSeed. ChainSeed service
code should also avoid SeedEmu compiler internals so that normal SeedEmu
compiler refactors do not unnecessarily break the extension.

## Current extraction

This is the first functional extraction from SeedEmu. The copied service
implementations retain their public behavior while using `chainseed.*` package
ownership. The extraction does not require SeedEmu to import ChainSeed.
Coordinated development may add generic extension APIs such as
`Node.setBaseImage` to SeedEmu, while blockchain implementations, images,
examples, and protocol tests remain owned by this repository.

Generated Compose output, client databases, raw benchmark samples, Xatu input
datasets, Python caches, and Node package caches are excluded. The extraction
source and exclusions are recorded in [MIGRATION_SOURCE.md](MIGRATION_SOURCE.md).

## Install for coordinated development

The recommended layout keeps both repositories as siblings under one workspace
directory:

```text
seed-workspace/
├── seed-emulator/
└── chain-seed/
```

The repositories do not technically depend on this location, but the sibling
layout makes cross-repository development and example reuse predictable. Never
clone ChainSeed inside the SeedEmu repository.

```bash
mkdir seed-workspace
cd seed-workspace

git clone https://github.com/BruceJqs/seed-emulator.git
git clone https://github.com/seed-labs/chain-seed.git

git -C seed-emulator switch development

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -e ./seed-emulator
python -m pip install -e "./chain-seed[dev]"
```

Run a local compile-only check or the complete Docker lifecycle with the same
entry point used by CI:

```bash
chainseed-test compile chain-seed/examples/ethereum/D01_ethereum_pos/example.yaml
chainseed-test all chain-seed/examples/ethereum/D01_ethereum_pos/example.yaml
```

Install the `fidelity` extra only when working on large-scale experiments:

```bash
python -m pip install -e "./chain-seed[dev,fidelity]"
```

Some ChainSeed examples reuse topology modules from SeedEmu's top-level
`examples` directory. The editable SeedEmu checkout makes those modules
available; run ChainSeed examples from the ChainSeed repository root.

Use explicit chain imports:

```python
from seedemu import Binding, Emulator, Filter
from chainseed.ethereum import ConsensusMechanism, EthereumService
from chainseed.monero import MoneroService
from chainseed.solana import SolanaService
```

## Version compatibility and initial 1.0.0 release

ChainSeed releases independently from SeedEmu. A ChainSeed release may adapt
to a newer SeedEmu without requiring a new SeedEmu release, while every
ChainSeed release declares the SeedEmu versions it supports.

During coordinated development, both repositories are installed from editable
checkouts and tested together. The current alpha metadata remains in place and
does not claim compatibility with an unreleased SeedEmu API.

The initial `1.0.0` release gate is:

1. Finish the coordinated changes in both repositories.
2. Pass local tests and both repositories' remote CI/CD.
3. Change both projects' release metadata to `1.0.0`.
4. Publish `v1.0.0` from each repository's current remote.
5. Reinstall both projects from those tags and rerun the compatibility suite.
6. Only then open the SeedEmu pull request against its upstream repository.

ChainSeed currently has no separate upstream remote and can be released from
its current remote. The SeedEmu upstream remote must not be pushed to or tagged
as part of this process.

After the coordinated release, ChainSeed should use a bounded requirement such
as `seedemu>=1.0.0,<2.0.0`, provided SeedEmu treats the public extension API as
semantic-versioned. A tested compatibility matrix belongs in each release.

## Layout

```text
src/chainseed/   Python service layers and protocol-aware testing
images/          Blockchain client and explorer image recipes
tools/           Explorer, wallet, and blockchain support tools
examples/        Small canonical examples grouped by protocol
experiments/     Reproducible large-scale definitions without raw output
tests/           Lightweight API checks and legacy migration references
docs/            Architecture and direct-cut migration design
```

The target architecture and remaining SeedEmu API work are documented in
[docs/design](docs/design/README.md).
