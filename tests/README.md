# Test Ownership

ChainSeed uses the same manifest-driven lifecycle as SeedEmu. Canonical tests
live beside their examples as `example.yaml` and `test_runtime.py`; the
`chainseed-test` command runs the compile, build, start, readiness, probe,
functional-test, and cleanup stages locally and in GitHub Actions.

For example:

```bash
chainseed-test compile examples/ethereum/D01_ethereum_pos/example.yaml
chainseed-test all examples/ethereum/D01_ethereum_pos/example.yaml
```

`test_public_imports.py` remains a lightweight package/API check. The files in
`protocol/` are legacy protocol tests retained as migration references; new
coverage belongs in example manifests and runtime programs.

ChainSeed CI checks out a selected SeedEmu revision and installs both projects
as editable packages. SeedEmu CI does not install or import ChainSeed.
