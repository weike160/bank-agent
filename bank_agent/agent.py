import json
import re
from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


CONFIRM_WORDS = {"确认", "确认执行", "同意", "yes", "confirm"}
REJECT_WORDS = {"取消", "拒绝", "不同意", "no", "reject"}


class AgentState(TypedDict, total=False):
    user_id: str
    session_id: str
    messages: Annotated[list[dict], add]
    tool_calls: list[dict]
    response: str
    steps: int


class BankAgent:
    """LangGraph orchestrator. All bank access goes through ToolRegistry."""

    def __init__(self, model, registry, banking_tools, checkpointer=None, max_steps=5):
        self.model = model
        self.registry = registry
        self.banking_tools = banking_tools
        self.max_steps = max_steps
        self.graph = self._build_graph().compile(checkpointer=checkpointer)

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("route_input", self._route_input)
        graph.add_node("call_model", self._call_model)
        graph.add_node("call_tools", self._call_tools)
        graph.add_edge(START, "route_input")
        graph.add_conditional_edges(
            "route_input", lambda state: "done" if state.get("response") else "model",
            {"done": END, "model": "call_model"},
        )
        graph.add_conditional_edges(
            "call_model", lambda state: "tools" if state.get("tool_calls") else "done",
            {"tools": "call_tools", "done": END},
        )
        graph.add_conditional_edges(
            "call_tools", self._after_tools,
            {"model": "call_model", "done": END},
        )
        return graph

    def chat(self, user_id, session_id, message):
        if not user_id or not session_id or not message.strip():
            raise ValueError("user_id, session_id and message are required")
        self.banking_tools.actions.create_session(user_id, session_id)
        result = self.graph.invoke(
            {
                "user_id": user_id,
                "session_id": session_id,
                "messages": [{"role": "user", "content": message}],
                "tool_calls": [],
                "response": "",
                "steps": 0,
            },
            config={"configurable": {"thread_id": session_id}},
        )
        return {"response": result["response"], "session_id": session_id}

    def _route_input(self, state):
        pending_result = self.banking_tools.get_pending_action(
            state["user_id"], state["session_id"]
        )
        pending = pending_result["data"] if pending_result["success"] else None
        if not pending:
            return {"response": "", "tool_calls": []}

        text = state["messages"][-1]["content"].strip().lower()
        if text in REJECT_WORDS:
            return self._action_response(
                self.banking_tools.reject_pending_action(state["user_id"], state["session_id"])
            )
        if pending["status"] == "WAITING_CONFIRMATION" and text in CONFIRM_WORDS:
            return self._action_response(
                self.banking_tools.confirm_pending_action(state["user_id"], state["session_id"])
            )
        if pending["status"] == "WAITING_STRONG_AUTH" and re.fullmatch(r"\d{6}", text):
            return self._action_response(self.banking_tools.verify_pending_action(
                state["user_id"], state["session_id"], text
            ))
        if pending["status"] == "WAITING_STRONG_AUTH" and text in {"发送验证码", "获取验证码", "send code"}:
            result = self.banking_tools.request_pending_verification(
                state["user_id"], state["session_id"]
            )
            if result["success"]:
                return {"response": "验证码已发送，请输入 6 位验证码。", "tool_calls": []}
            return {"response": f'验证码发送失败：{result["error"]}', "tool_calls": []}
        return {"response": self._pending_prompt(pending), "tool_calls": []}

    def _call_model(self, state):
        if state.get("steps", 0) >= self.max_steps:
            return {"response": "本轮工具调用次数过多，已安全停止。", "tool_calls": []}
        reply = self.model.respond(state["messages"], self.registry.definitions())
        calls = reply.get("tool_calls") or []
        assistant_message = {"role": "assistant", "content": reply.get("content", "")}
        if calls:
            assistant_message["tool_calls"] = [{
                "id": call.get("id", ""),
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=False),
                },
            } for call in calls]
        return {
            "messages": [assistant_message],
            "response": reply.get("content", "") if not calls else "",
            "tool_calls": calls,
            "steps": state.get("steps", 0) + 1,
        }

    def _call_tools(self, state):
        messages = []
        for call in state["tool_calls"]:
            result = self.registry.call(
                call.get("name", ""), call.get("arguments", {}),
                state["user_id"], state["session_id"],
            )
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "name": call.get("name", ""),
                "content": json.dumps(result, ensure_ascii=False),
            })
        pending = self.banking_tools.get_pending_action(
            state["user_id"], state["session_id"]
        )
        response = ""
        if pending["success"] and pending["data"]:
            response = self._pending_prompt(pending["data"])
        return {"messages": messages, "tool_calls": [], "response": response}

    def _after_tools(self, state):
        return "done" if state.get("response") else "model"

    @staticmethod
    def _pending_prompt(action):
        details = json.dumps(action.get("payload", {}), ensure_ascii=False, sort_keys=True)
        if action["status"] == "WAITING_CONFIRMATION":
            return f"操作等待确认：{action['action_type']} {details}。请明确回复“确认”或“取消”。"
        return f"操作需要强验证：{action['action_type']} {details}。请回复“发送验证码”或“取消”。"

    @staticmethod
    def _action_response(result):
        if not result["success"]:
            return {"response": f'操作失败：{result["error"]}', "tool_calls": []}
        action = result["data"]
        status = action.get("status", "UNKNOWN")
        return {"response": f"操作状态：{status}", "tool_calls": []}
