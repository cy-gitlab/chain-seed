# ChainSeed

中文 | [English](README.md)

ChainSeed 是 [SeedEmu](https://github.com/seed-labs/seed-emulator) 的区块链
协议扩展仓库，负责 Ethereum、Monero、Solana 和 Chainlink 服务层，以及
对应的镜像配方、工具、示例、运行时探针和大规模实验定义。

依赖方向固定为：

```text
chain-seed -> seed-emulator
seed-emulator -X-> chain-seed
```

## 依赖关系与前置条件

ChainSeed 是 SeedEmu 的附加扩展，并不是能够单独使用的替代品。用户必须先
准备好可用的 SeedEmu 源码工作区和 Python 环境。在加入 ChainSeed 前，用户
应能在同一环境中成功渲染、编译、构建并运行一个不包含区块链的 SeedEmu
示例。

ChainSeed 可以导入 `seedemu` 已公开且有文档约束的 API。反方向上，SeedEmu
不得导入或打包 ChainSeed。同时，ChainSeed 服务代码应避免依赖 SeedEmu
编译器的私有实现，以免 SeedEmu 的正常内部重构不必要地破坏扩展。

## 当前迁移状态

这是从 SeedEmu 提取出的第一版可运行实现。复制后的服务保持原有公开行为，
但 Python 包归属改为 `chainseed.*`。拆分不要求 SeedEmu 导入 ChainSeed。
联合开发可以向 SeedEmu 增加 `Node.setBaseImage` 这样的通用扩展接口，但
区块链实现、镜像、示例和协议测试仍由本仓库负责。

仓库不复制生成的 Compose 输出、客户端数据库、大规模原始采样、Xatu 输入
数据、Python 缓存和 Node 依赖缓存。来源与排除项记录在
[MIGRATION_SOURCE.md](MIGRATION_SOURCE.md)。

## 联合开发安装

推荐将两个仓库放在同一个工作目录下并保持为兄弟目录：

```text
seed-workspace/
├── seed-emulator/
└── chain-seed/
```

两个仓库在技术上不依赖固定位置，但兄弟目录更便于跨仓库开发和示例复用。
不要把 ChainSeed clone 到 SeedEmu 仓库内部，以免形成嵌套 Git 仓库。

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

本地编译检查和完整 Docker 生命周期使用与 CI 相同的入口：

```bash
chainseed-test compile chain-seed/examples/ethereum/D01_ethereum_pos/example.yaml
chainseed-test all chain-seed/examples/ethereum/D01_ethereum_pos/example.yaml
```

只有进行大规模实验时才需要安装 `fidelity` 可选依赖：

```bash
python -m pip install -e "./chain-seed[dev,fidelity]"
```

部分 ChainSeed 示例会复用 SeedEmu 顶层 `examples` 目录中的拓扑模块。
ChainSeed 会通过已安装的 `seedemu` 包定位这些模块，因此 SeedEmu 必须从
包含 `examples` 目录的源码 checkout 进行 editable 安装，无需手动设置
`PYTHONPATH`。

迁移后使用显式导入：

```python
from seedemu import Binding, Emulator, Filter
from chainseed.ethereum import ConsensusMechanism, EthereumService
from chainseed.monero import MoneroService
from chainseed.solana import SolanaService
```

## 版本兼容与首次 1.0.0 发布

ChainSeed 与 SeedEmu 独立发布。ChainSeed 可以发布新版本以适配新版
SeedEmu，而不要求 SeedEmu 同步发版；每个 ChainSeed 版本都必须明确声明
其支持的 SeedEmu 版本范围。

联合开发期间，两个仓库都通过 editable checkout 安装并一起测试。当前仍
保留 alpha 版本元数据，不提前声称兼容尚未发布的 SeedEmu API。

首次 `1.0.0` 的发布门禁是：

1. 完成两个仓库中的配套修改；
2. 本地测试和两个仓库的远端 CI/CD 全部通过；
3. 将两个项目的发布版本统一修改为 `1.0.0`；
4. 分别从两个仓库的当前远端发布 `v1.0.0`；
5. 从这两个 tag 重新安装并运行完整兼容性测试；
6. 再确认无误后，才向 SeedEmu 的 upstream 提交 PR。

ChainSeed 当前没有单独的 upstream，可以直接从当前远端发布。此过程中不得
向 SeedEmu upstream 推送 commit 或 tag。

联合发布后，ChainSeed 应使用有上下界的依赖，例如
`seedemu>=1.0.0,<2.0.0`；前提是 SeedEmu 对公共扩展 API 遵守语义化版本。
每次发布都应提供经过测试的兼容矩阵。

目录职责：

```text
chainseed/   Python 服务层和协议感知测试
images/          区块链客户端与 Explorer 镜像配方
tools/           Explorer、钱包和区块链工具
examples/        按协议分类的小型规范示例
experiments/     不包含原始输出的大规模可复现实验
tests/           轻量 API 检查和旧测试迁移参考
docs/            架构与直接切换迁移设计
```

目标架构和仍需补充的 SeedEmu 公共 API 见
[docs/design](docs/design/README.zh-CN.md)。
