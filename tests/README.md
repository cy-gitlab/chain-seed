# Test Ownership

All blockchain-specific tests are owned and executed by ChainSeed.

- `test_public_imports.py` checks the public package and extension descriptor.
- `protocol/` contains Ethereum, Monero, Solana, and Chainlink behavior and
  runtime tests migrated from SeedEmu.

ChainSeed changes are tested against a pinned supported SeedEmu revision.
SeedEmu changes are tested by passing the candidate SeedEmu revision into the
ChainSeed compatibility suite. SeedEmu itself does not install, import, check
out, or test ChainSeed.
