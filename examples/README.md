# Blockchain Examples

The canonical SeedEmu blockchain examples are maintained here and grouped by
protocol:

- `ethereum/`: PoA, PoS, explorer, faucet, contract, oracle, validator, and
  reusable blockchain examples
- `monero/`: Monero network example
- `solana/`: Solana network example
- `chainlink/`: Chainlink on an Ethereum PoA network

Shared network definitions live in `ethereum/D00_ethereum_poa/mini_internet.py`.
Ethereum, Monero, and Solana examples reuse them through public SeedEmu APIs.
No SeedEmu source checkout or manual `PYTHONPATH` is required.

Run examples from the ChainSeed repository root with both repositories
installed in the same Python environment:

```bash
python -m pip install /path/to/seed-emulator
python -m pip install .
python examples/ethereum/D01_ethereum_pos/ethereum_pos.py
```

Generated `output/` directories are intentionally not versioned.

Canonical Ethereum examples have `example.yaml` manifests. Run compilation
without starting containers, or opt into the Docker lifecycle once configured:

```bash
chainseed-test compile examples/ethereum/D01_ethereum_pos/example.yaml
chainseed-test all examples/ethereum/D01_ethereum_pos/example.yaml
```

Export a reusable, unrendered network:

```bash
python examples/ethereum/D00_ethereum_poa/mini_internet.py \
  --topology hybrid --hosts-per-as 3 --dumpfile /tmp/network.bin
```

Other Python examples can call `mini_internet.build_emulator()` directly, or
load the exported network with `Emulator.load()`. D00's `run()` accepts
`network_dumpfile` to bind its blockchain onto an existing network dump.
