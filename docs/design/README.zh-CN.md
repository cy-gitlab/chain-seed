# ChainSeed 拆分设计

中文 | [English](README.md)

## 决策

区块链支持将在一次协调发布中从 `seed-emulator` 整体迁移到独立的
`chain-seed` 仓库。不设置兼容期，也不为原有 `seedemu.services.*`
模块提供导入兼容层。

依赖方向固定如下：

```text
chain-seed -> seed-emulator
seed-emulator -X-> chain-seed
```

`seed-emulator` 提供网络仿真平台，`chain-seed` 提供区块链服务、镜像、
工具、示例、实验和可观测性扩展。

## 文档

- [目标架构](architecture.zh-CN.md)：仓库边界、模块、扩展契约、运行时、
  制品和可观测性。
- [SeedEmu 集成契约](seedemu-integration.zh-CN.md)：外部 ChainSeed
  软件包需要的公共接口和核心改动。
- [直接切换迁移](migration.zh-CN.md)：源码映射、实施顺序、联合发布、
  CI 和验收条件。

英文版本：

- [Architecture](architecture.md)
- [SeedEmu integration](seedemu-integration.md)
- [Direct-cut migration](migration.md)

## 设计规则

1. `chain-seed` 只能导入文档明确规定的 SeedEmu 公共扩展 API。
2. `seed-emulator` 不得导入或打包 ChainSeed。
3. 区块链规范示例与测试只能存在于 `chain-seed`。
4. Python 包和 OCI 镜像使用兼容且不可变的发布版本。
5. 大规模实验结果通过清单引用，不提交到 Git。
6. 直接切换后，不再兼容包含旧区块链类的 pickle 仿真器文件。

## 非目标

- 保留 `from seedemu import EthereumService`。
- 在两个仓库中维护重复的区块链实现。
- 在拉取请求 CI 中运行 300 节点实验。
- 在 Git 中存储生成镜像或大规模原始实验数据。

## 参考仓库

- [seed-visualization](https://github.com/seed-labs/seed-visualization)：
  通过 `emulator-service` 与仿真器连接的独立可视化仓库。
- [skyseed](https://github.com/seed-labs/skyseed)：
  用于参考项目组织形式的独立 SEED 扩展仓库。

