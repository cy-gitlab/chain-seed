# SeedEmu Integration Contract

[中文](seedemu-integration.zh-CN.md) | English

## SeedEmu After the Cut

```text
seedemu/
├── core/
│   ├── Emulator
│   ├── Layer
│   ├── Service
│   ├── Server
│   ├── Node
│   ├── Registry
│   └── extension
├── layers/
├── compiler/
├── hooks/
├── mergers/
├── services/
│   └── protocol-independent services
└── testing/
    ├── base
    └── runtime
```

The package contains no blockchain client, chain configuration, blockchain
test probe, explorer, or blockchain-specific dependency.

## Integration Model

ChainSeed classes remain ordinary external subclasses of SeedEmu `Layer`,
`Service`, and `Server`. Users construct them explicitly and add them to an
`Emulator`; SeedEmu does not dynamically import or instantiate ChainSeed.

```mermaid
sequenceDiagram
    participant User
    participant Chain as ChainSeed Layer
    participant Emu as SeedEmu Emulator
    participant Node as SeedEmu Node
    participant Compiler as SeedEmu Compiler

    User->>Chain: Construct service
    User->>Emu: addLayer(service)
    Emu->>Chain: configure(emulator)
    Chain->>Emu: Resolve bindings and dependencies
    Emu->>Chain: render(emulator)
    Chain->>Node: Declare image, files, labels and commands
    User->>Emu: compile(compiler, output)
    Emu->>Compiler: Compile generic node model
```

Python entry-point discovery reports installed extensions and validates
artifacts. It does not replace explicit imports.

## Public API Version 1

### Emulator and Registry

| Operation | Purpose |
|---|---|
| `addLayer(layer)` | Register an external layer |
| `getLayer(name)` | Resolve a declared layer dependency |
| `getBindingFor(vnode)` | Resolve a virtual blockchain role to a physical node |
| `getServerByVirtualNodeName(vnode)` | Resolve another service's virtual server |
| `getRegistry()` | Inspect rendered nodes through stable registry queries |
| `requireExtension(descriptor)` | Record artifact provenance and requirements |

External services must not depend on Registry key layout or private Emulator
fields.

### Layer and Service

| Operation | Purpose |
|---|---|
| `addDependency(name, optional, configure)` | Declare render ordering |
| `install(vnode)` | Create a virtual server |
| `configure(emulator)` | Resolve cross-layer inputs |
| `render(emulator)` | Attach the service to bound nodes |
| `getTargets()` | Read rendered server/node pairs |

Layer names are stable identifiers because dependency ordering and existing
cross-service lookup use them. ChainSeed owns its names, including
`EthereumService`, `MoneroService`, `SolanaService`, and `ChainlinkService`.

### Node Specification

| Operation | Purpose |
|---|---|
| `addSoftware(packages)` | Declare host packages |
| `addBuildCommand(command)` | Declare image build steps |
| `setFile(path, content)` | Add generated configuration |
| `importFile(host, guest)` | Add a repository/runtime artifact |
| `appendStartCommand(command, fork)` | Add process startup |
| `setLabel(key, value)` | Expose role and discovery metadata |
| `addPersistentStorage(path)` | Declare persistent state |
| `addSharedFolder(host, guest)` | Mount an experiment artifact |
| `addPortForwarding(host, container)` | Expose RPC or UI |
| `setBaseImage(image_ref)` | Select an immutable external image |

`setBaseImage` stores a compiler-neutral `ImageRef` on the node:

```text
registry, repository, tag, digest, platform
```

Only `repository` and either `tag` or `digest` are required. Release manifests
use digests. Docker-specific objects and image-priority lists remain private to
the Docker compiler.

### Runtime Testing

SeedEmu retains generic operations:

- discover containers and inspect generic labels;
- map container PIDs and enter namespaces;
- execute a command or HTTP probe;
- read resource counters and packet captures.

ChainSeed owns protocol interpretation, chain-health probes, transaction
generation, block/finality checks, and blockchain result schemas.

## Extension Descriptor

SeedEmu defines a small immutable descriptor:

```text
distribution
version
extension_api_version
owned_layer_names
artifact_schema_versions
```

When ChainSeed registers its first layer, the descriptor is attached to the
Emulator. Compilation emits it into `seedemu-manifest.json`; dump serialization
includes the same requirement.

Duplicate ownership of a layer name is an error. An unsupported extension API
version fails before configure or load.

## Dump Format

Raw Registry pickle files are replaced by an envelope with a readable header
and a checksummed payload:

```mermaid
flowchart LR
    Dump["Emulator.dump"]
    Header["JSON header: core and extensions"]
    Payload["Serialized registry payload"]
    File["Versioned dump artifact"]
    Load["Emulator.load"]
    Validate["Validate versions and imports"]

    Dump --> Header
    Dump --> Payload
    Header --> File
    Payload --> File
    File --> Load
    Load --> Validate
    Validate --> Payload
```

Validation occurs before deserialization so a missing ChainSeed installation
produces an actionable error. The format is generic and may support other
external service repositories.

## Compiler Boundary

The compiler consumes only the rendered generic node model. It must not import
ChainSeed or contain chain-specific image selection.

```mermaid
flowchart LR
    Service["External service"]
    Model["Generic Node model"]
    Docker["Docker compiler"]
    Other["Other compiler"]
    Output["Deployment artifact"]

    Service --> Model
    Model --> Docker
    Model --> Other
    Docker --> Output
    Other --> Output
```

The current compiler-specific Monero image override is replaced by
`Node.setBaseImage`. This is the only known blockchain service dependency that
crosses the intended compiler boundary.

## Package and Import Rules

Allowed:

```python
from seedemu.core import Emulator, Node, Service, Server
from seedemu.core.enums import NetworkType
```

Forbidden:

```python
from seedemu import *
from seedemu.compiler.DockerImage import DockerImage
from seedemu.services.EthereumService import EthereumService
```

SeedEmu public modules may not import `chainseed`, inspect a ChainSeed package
path, or special-case a blockchain layer name.

## ChainSeed-owned Compatibility Test

A SeedEmu candidate is compatible when the ChainSeed-owned suite can:

1. import all public extension interfaces;
2. render a minimal Ethereum PoS topology;
3. compile it with the Docker compiler;
4. emit the expected extension and image manifests;
5. load a new-format dump when ChainSeed is installed;
6. reject that dump with a clear error when ChainSeed is absent.

The test code and execution workflow live only in ChainSeed. SeedEmu CI does
not install, import, or check out ChainSeed. A candidate SeedEmu revision is
passed to the ChainSeed workflow by an external trigger or a release gate.
