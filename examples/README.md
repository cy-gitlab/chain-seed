# Blockchain Examples

The canonical SeedEmu blockchain examples are maintained here and grouped by
protocol:

- `ethereum/`: PoA, PoS, explorer, faucet, contract, oracle, validator, and
  reusable blockchain examples
- `monero/`: Monero network example
- `solana/`: Solana network example
- `chainlink/`: Chainlink on an Ethereum PoA network

The D00 PoA and D31 Chainlink examples reuse
`examples/internet/B03_hybrid_internet` from the editable SeedEmu checkout.
ChainSeed does not duplicate SeedEmu's network-topology examples.

Run examples from the ChainSeed repository root with both repositories
installed in the same Python environment:

```bash
python -m pip install -e /path/to/seed-emulator
python -m pip install -e .
python examples/ethereum/D01_ethereum_pos/ethereum_pos.py
```

Generated `output/` directories are intentionally not versioned.

Each migrated example has an `example.yaml` manifest. Run the same lifecycle
used by GitHub Actions with:

```bash
chainseed-test compile examples/ethereum/D01_ethereum_pos/example.yaml
chainseed-test all examples/ethereum/D01_ethereum_pos/example.yaml
```
