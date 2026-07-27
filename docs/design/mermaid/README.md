# Mermaid Rendering

## 中文

安装锁定版本的渲染器并验证全部图表：

```bash
cd docs/design/mermaid
npm ci
npm run render
```

生成的 `.mmd` 和 `.svg` 文件位于 `rendered/`，该目录已被 Git 忽略。
任意 Mermaid 代码块无效时，命令都会以非零状态退出。

`puppeteer-config.json` 关闭 Chromium 进程沙箱，以便无头浏览器可以在
受限开发环境中运行。该配置仅影响此文档渲染命令。

## English

Install the pinned renderer and validate every diagram:

```bash
cd docs/design/mermaid
npm ci
npm run render
```

Generated `.mmd` and `.svg` files are written to `rendered/` and are ignored by
Git. Any invalid Mermaid block causes the command to exit with a non-zero
status.

`puppeteer-config.json` disables the Chromium process sandbox because the
renderer runs inside a restricted development environment. It affects only
this headless documentation command.
