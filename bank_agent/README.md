# Bank Agent

LangGraph 只负责编排。银行调用必须经过 `ToolRegistry`，修改类操作必须经过 `ActionService`。

第一版支持：

- 查询类工具调用
- 黄色操作明确确认
- 红色操作发送并校验 OTP
- 按 `session_id` 保存 LangGraph 对话状态
- 每轮最多 5 次模型调用
- Supervisor 将请求路由到账户分析、银行业务或理财 Agent
- 方向键上下翻看历史输入，方向键左右移动光标
- 脱敏 JSONL 轨迹日志

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

## 排查日志

CLI 将轨迹追加到 `bank_agent/logs/agent.jsonl`。每行是一条 JSON 事件，包含：

- 用户输入和实际发送给模型的上下文
- Supervisor 路由结果和简短决策说明
- 模型可见输出和 Tool Call
- 工具参数、工具真实结果及最终回复
- 待确认 Action 输入和运行错误

OTP、credential 等敏感字段会自动遮盖。日志不会记录模型不可见的内部思维链。
