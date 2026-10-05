# 开发与维护

仓库地址：https://github.com/BrucePayton/agenthost2sqlbot

使用 Python 3.12 和 Node.js 22，在项目根目录安装锁定依赖：

```bash
uv sync --frozen
npm ci
```

从 `main` 创建功能分支，完成改动与相关测试后提交 Pull Request。合并前检查差异，禁止提交真实环境变量、数据库、会话记录、密钥和本机运行产物。配置示例使用占位值；第三方代码保留原有许可证。

基础 CI 在推送到 `main` 和 Pull Request 时运行：

```bash
npm run check:contracts
npm run test:js
uv run --frozen pytest -q tests/test_runtime_import_boundary.py tests/test_runtime_contracts.py
```

这些检查不需要真实模型凭据，不能替代完整测试或集成验收。根据变更范围运行其他相关测试；布局算法和 Davinci 集成变更必须遵循 [AGENTS.md](AGENTS.md) 的真实验收要求。需要 Docker、浏览器、配对仓库或真实凭据的验收单独执行，未执行时明确记录原因。

本地启动和部署配置见 [README.md](README.md)。项目采用 [MIT License](LICENSE)，第三方依赖遵循各自许可证。
