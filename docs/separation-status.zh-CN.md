# SeedEmu / ChainSEED 适配状态

## 当前范围（2026-10-15）

按最新安排，SeedEmu 已从 `origin`（seed-labs/seed-emulator）拉取，切换到
跟踪 `origin/Docker` 的本地 `Docker` 分支，且执行了仅快进 pull。
通用 Docker 仿真网络适配现在由该分支的 `seedemu/compiler/Docker.py`
实现；ChainSEED 已删除 `_compileNet()` 临时覆写，直接继承 SeedEmu。
修改保留在工作区，尚未提交或推送。此前分离改动的 Git stash 仍保留。

ChainSEED 发行名为 `chainseed`，依赖 `seedemu>=0.0.7,<0.1`，在导入时
检查实际需要的 `SystemProfile` 和 Docker 扩展钩子，不要求上游新增扩展版本标记。
版本号不足以区分具有这些接口的 当前配套分支与旧发行构建。

## 安装方式及上游限制

当前使用 Python 3.10，在干净虚拟环境中先安装 SeedEmu Docker，
再安装 ChainSEED：

```bash
python -m pip install -e ../seed-emulator
python -m pip install .
```

SeedEmu Docker 的普通 wheel 缺少旧 Ethereum 模板资源；由于上游
在导入时仍会加载旧区块链服务，普通 wheel 导入会失败。使用 editable
安装以读取源码资源，保持源码目录。网络修复已在 SeedEmu 源码中实现；ChainSEED 不向 SeedEmu 安装目录注入文件。CI 同样采用上游 editable 加 ChainSEED wheel。

## 已保留的 ChainSEED 能力

- Ethereum、Monero、Solana、Chainlink、独立系统镜像映射和浏览器组件。
- D00 [mini_internet.py](../examples/ethereum/D00_ethereum_poa/mini_internet.py)
  的共享拓扑和未 render 的网络 `.bin` 导出、复用；不向 SeedEmu 添加 ExampleTopology。
- 迁入的工具、文档及历史实验，来源见 [迁移清单](migration-inventory.json)。
  清单反映上一轮迁移来源，并不要求删除 SeedEmu 中的对应文件。
- 检查 ChainSEED 不依赖上游旧区块链接口，而不要求上游删除旧实现。

## 验证及后续

使用 SeedEmu Docker 提交 `964d1665b99aee2a915f3c6e22a4248cd01a1837`
的源码副本进行 editable 安装，ChainSEED 安装普通 wheel：14 项配置/API
测试及 2 个子测试通过。后续静态回归扩展到 22 项测试及 2 个子测试。

2026-10-14 已在 WSL / Docker 29.9.0 完成 D01 动态验证：就绪检查、
真实 Lighthouse 进程检查、Geth 出块（高度 5 → 6）及签名 ETH 转账均通过。
转账打包在第 7 块，接收方余额增加 1 ETH。结果位于
`/tmp/chainseed-d01-fixed`（历史记录）；其他示例尚未动态验证。

本次阻塞来自 Docker bridge 对跨网段仿真流量的过滤。SeedEmu 的 Docker 编译器
为 Local/InternetExchange 网络生成 `gateway_mode_ipv4=nat-unprotected`，
需要 Docker Engine 28+；外部 Bridge 网络保持默认。该模式允许直接路由
访问未发布端口，限用于实验网络。修复位于 SeedEmu Docker 分支，宿主机全局防火墙保持不变。
同时修复六个规范示例的 Lighthouse 探针，避免匹配到检查命令自身。

后续包括上游普通 wheel 资源修复、Python 3.11+ 依赖升级、历史实验大文件
恢复，以及用户提供的交易发生器和实验代码迁入。扩展自动发现、编译 manifest
扩展信息和带版本的 dump 封装仍属于计划，见 [集成契约](design/seedemu-integration.zh-CN.md)。

2026-10-15 迁移后的静态检查：SeedEmu 通用网络回归 6 项通过，覆盖
Local/InternetExchange/Bridge 及 self-managed 模式；ChainSEED 回归
22 项及 2 个子测试通过。独立安装后的 ChainSeedDocker 与 SeedEmu Docker
使用同一个 `_compileNet()` 方法。重新编译的 D01 配置中，22 个网络与
先前运行验证通过的配置一致。CI 默认检出 seed-labs/seed-emulator 的 Docker 分支。

迁移后的 D01 动态复查通过：Geth 高度从 0 增长到 1，签名 ETH 转账
及余额校验通过，记录保存在 `/tmp/chainseed-seedemu-docker-validation`。
首次复用了非正常关闭的旧容器，遇到数据库恢复问题；保存日志并重新创建
测试容器后通过。验证完成后保存容器日志并清理测试环境，未重建镜像。
