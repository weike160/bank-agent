# Bank Agent

银行接口实验项目。

- `mock_bank/`：可持久化的模拟银行 API
- `bank_gateway/`：统一封装 Mock Bank HTTP 接口
- `actions/`：可复用的红黄绿权限、确认、强验证和审计模块
- `tools/`：提供给 Agent 的结构化银行工具
- `bank_agent/`：基于 LangGraph 的 Agent 编排层

Mock Bank 的启动和接口说明见 [`mock_bank/README.md`](mock_bank/README.md)。

Bank Gateway 的用法见 [`bank_gateway/README.md`](bank_gateway/README.md)。

Action & Security 的用法见 [`actions/README.md`](actions/README.md)。

Banking Tools 的说明见 [`tools/README.md`](tools/README.md)。

Bank Agent 的说明见 [`bank_agent/README.md`](bank_agent/README.md)。

## 快速开始

安装 [uv](https://docs.astral.sh/uv/)：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安装项目环境：

```bash
uv sync --frozen
```

这会自动使用 Python 3.12，并把依赖安装到项目内的 `.venv`，不影响系统环境。

## 测试 Agent

```bash
# 首次运行：创建本地配置并填写 LLM_API_KEY
cp .env.example .env

# 终端 1
uv run python mock_bank/app.py

# 终端 2
uv run python -m bank_agent.cli
```

`.env` 只保存在本机且已被 Git 忽略；`.env.example` 不包含真实密钥，可以安全提交。
