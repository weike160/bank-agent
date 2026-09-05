# Bank Agent

LangGraph 只负责编排。银行调用必须经过 `ToolRegistry`，修改类操作必须经过 `ActionService`。

第一版支持：

- 查询类工具调用
- 黄色操作明确确认
- 红色操作发送并校验 OTP
- 按 `session_id` 保存 LangGraph 对话状态
- 每轮最多 5 次模型调用

模型适配器实现 `respond(messages, tools)`，返回：

```python
{"content": "", "tool_calls": [{"id": "1", "name": "get_balance", "arguments": {"account_id": "A1001"}}]}
```

`user_id` 和 `session_id` 必须由登录后的服务端注入，不能取自模型输出。

当前测试使用 `MemorySaver`。实际服务建议使用 `langgraph-checkpoint-sqlite` 的 `SqliteSaver`；多实例部署时改用 PostgreSQL checkpointer。

## 使用 Agnes 测试

先启动 Mock Bank：

```bash
uv run python mock_bank/app.py
```

首次运行时，在仓库根目录创建本地配置并填入密钥：

```bash
cp .env.example .env
```

另开一个终端启动 Agent：

```bash
uv run python -m bank_agent.cli
```

`.env.example` 包含 Agnes、OpenAI 和 DeepSeek 配置示例。默认模型是 `agnes-2.5-flash`。开发模式的 OTP 会打印在 Agent 终端。
