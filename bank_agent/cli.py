import os
import sqlite3
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from actions import OTPVerifier
from bank_agent.agent import BankAgent
from bank_agent.model import OpenAICompatibleModel
from bank_agent.prompts import SYSTEM_PROMPT
from bank_agent.trace import TraceLogger
from bank_gateway import BankGateway
from tools import BankingTools, create_bank_action_service, create_banking_tool_registry


ROOT = Path(__file__).resolve().parent


def main():
    load_dotenv(ROOT.parent / ".env")
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("AGNES_API_KEY")
    if not api_key:
        raise SystemExit("请先在 .env 中设置 LLM_API_KEY")

    user_id = os.environ.get("BANK_USER_ID", "U4")
    session_id = os.environ.get("BANK_SESSION_ID", "SES" + uuid.uuid4().hex[:12].upper())
    gateway = BankGateway(os.environ.get("BANK_BASE_URL", "http://127.0.0.1:8000"))
    otp = OTPVerifier(
        lambda user, code: print(f"[仅开发环境] {user} 的验证码：{code}"),
        ROOT / "otp.db",
    )
    actions = create_bank_action_service(gateway, ROOT / "actions.db", otp)
    banking_tools = BankingTools(gateway, actions)
    registry = create_banking_tool_registry(banking_tools)
    model = OpenAICompatibleModel(
        api_key,
        os.environ.get("LLM_MODEL") or os.environ.get("AGNES_MODEL", "agnes-2.5-flash"),
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1"),
        system_prompt=SYSTEM_PROMPT,
    )
    connection = sqlite3.connect(ROOT / "checkpoints.db", check_same_thread=False)
    trace_path = ROOT / "logs" / "agent.jsonl"
    trace = TraceLogger(trace_path)
    agent = BankAgent(model, registry, banking_tools, SqliteSaver(connection), trace=trace)
    prompt = PromptSession(history=FileHistory(str(ROOT / "cli_history")))

    print(
        f"Bank Agent 已启动（用户 {user_id}，会话 {session_id}），输入 exit 退出。\n"
        f"轨迹日志：{trace_path}"
    )
    try:
        while True:
            try:
                message = prompt.prompt("你：").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if message.lower() in {"exit", "quit"}:
                break
            if message:
                try:
                    response = agent.chat(user_id, session_id, message)["response"]
                    print("Agent：" + response)
                except RuntimeError as error:
                    trace.write(
                        "agent_error", user_id, session_id,
                        error_type=type(error).__name__, error=str(error),
                    )
                    print(f"Agent：{error}。你可以继续输入或稍后重试。")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
