# ChainSeed Separation Design

[中文](README.zh-CN.md) | English

## Decision

Blockchain support moves from `seed-emulator` to the independent
`chain-seed` repository in one coordinated release. There is no compatibility
period and no import shim for the old `seedemu.services.*` modules.

The dependency direction is fixed:

```text
chain-seed -> seed-emulator
seed-emulator -X-> chain-seed
```

`seed-emulator` provides the network-emulation platform. `chain-seed` provides
blockchain services, images, tools, examples, experiments, and observability
extensions.

## Documents

- [Architecture](architecture.md): repository boundaries, modules, extension
  contract, runtime, artifacts, and observability.
- [SeedEmu integration](seedemu-integration.md): the exact public interfaces and
  core changes required by an external ChainSeed package.
- [Direct-cut migration](migration.md): source mapping, implementation order,
  release coordination, CI, and acceptance criteria.

## Design Rules

1. `chain-seed` imports only the documented public SeedEmu extension API.
2. `seed-emulator` never imports or packages ChainSeed.
3. Canonical blockchain examples and tests live only in `chain-seed`.
4. Python packages and OCI images use compatible, immutable release versions.
5. Large experiment results are referenced by manifests, not committed to Git.
6. Old pickled emulators containing blockchain classes are intentionally
   incompatible after the direct cut.

## Non-goals

- Preserving `from seedemu import EthereumService`.
- Keeping duplicate blockchain implementations in both repositories.
- Running 300-node experiments in pull-request CI.
- Storing generated images or large raw experiment datasets in Git.

## Reference Repositories

- [seed-visualization](https://github.com/seed-labs/seed-visualization):
  independent visualization repository connected through `emulator-service`.
- [skyseed](https://github.com/seed-labs/skyseed): independent SEED extension
  repository referenced for project organization.
