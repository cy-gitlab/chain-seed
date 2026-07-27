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

## 当前迁移状态

这是从 SeedEmu 提取出的第一版可运行实现。复制后的服务保持原有公开行为，
但 Python 包归属改为 `chainseed.*`。源 SeedEmu 工作区中的文件没有被删除、
移动或修改。

仓库不复制生成的 Compose 输出、客户端数据库、大规模原始采样、Xatu 输入
数据、Python 缓存和 Node 依赖缓存。来源与排除项记录在
[MIGRATION_SOURCE.md](MIGRATION_SOURCE.md)。

## 开发安装

```bash
python -m pip install -e /path/to/seed-emulator
python -m pip install -e ".[dev,fidelity]"
```

迁移后使用显式导入：

```python
from seedemu import Binding, Emulator, Filter
from chainseed.ethereum import ConsensusMechanism, EthereumService
from chainseed.monero import MoneroService
from chainseed.solana import SolanaService
```

目录职责：

```text
src/chainseed/   Python 服务层和协议感知测试
images/          区块链客户端与 Explorer 镜像配方
tools/           Explorer、钱包和区块链工具
examples/        按协议分类的小型规范示例
experiments/     不包含原始输出的大规模可复现实验
tests/           ChainSeed 导入、链协议行为和 SeedEmu 兼容性测试
docs/            架构与直接切换迁移设计
```

目标架构和仍需补充的 SeedEmu 公共 API 见
[docs/design](docs/design/README.zh-CN.md)。
