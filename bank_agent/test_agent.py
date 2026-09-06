import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from actions import OTPVerifier
from bank_agent import BankAgent
from tools import BankingTools, create_bank_action_service, create_banking_tool_registry


class FakeGateway:
    def __init__(self):
        self.balance = "5000.00"
        self.executions = 0

    def get_accounts(self, user_id):
        return {"accounts": [{"id": "A1", "balance": self.balance}]}

    def get_balance(self, account_id):
        return {"account": {"id": account_id, "balance": self.balance}}

    def get_transactions(self, account_id, start_date=None, end_date=None, limit=100):
        return {"transactions": []}

    def prepare_transfer(self, source, target, amount, note):
        return {"transfer": {"id": "T1"}}

    def execute_transfer(self, transfer_id):
        self.executions += 1
        return {"transfer": {"id": transfer_id, "status": "completed"}}


class FakeModel:
    def __init__(self, replies):
        self.replies = iter(replies)

    def respond(self, messages, tools):
        if tools and tools[0]["function"]["name"] == "route_to_agent":
            text = messages[-1]["content"]
            agent = "operations" if "转账" in text else "account"
            return {"content": "", "tool_calls": [{
                "id": "route", "name": "route_to_agent",
                "arguments": {"agent": agent, "reason": "test route"},
            }]}
        return next(self.replies)


class BankAgentTest(unittest.TestCase):
    def build(self, replies, code="123456"):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        gateway = FakeGateway()
        otp = OTPVerifier(lambda user, sent: None, root / "otp.db", code_generator=lambda: code)
        actions = create_bank_action_service(gateway, root / "actions.db", otp)
        banking = BankingTools(gateway, actions)
        registry = create_banking_tool_registry(banking)
        return BankAgent(FakeModel(replies), registry, banking, MemorySaver()), gateway

    def test_query_uses_registry_and_model_summarizes_result(self):
        agent, _ = self.build([
            {"content": "", "tool_calls": [{"id": "1", "name": "get_balance", "arguments": {"account_id": "A1"}}]},
            {"content": "余额为 5000.00 元。", "tool_calls": []},
        ])
        result = agent.chat("U1", "S1", "余额是多少")
        self.assertEqual(result["response"], "余额为 5000.00 元。")
        self.assertEqual(result["specialist"], "account")

    def test_yellow_transfer_waits_for_exact_confirmation(self):
        agent, gateway = self.build([{
            "content": "", "tool_calls": [{"id": "1", "name": "request_transfer", "arguments": {
                "source_account_id": "A1", "target_account_id": "A2", "amount": "200.00"
            }}],
        }])
        first = agent.chat("U1", "S1", "转账 200")
        self.assertIn("等待确认", first["response"])
        self.assertEqual(gateway.executions, 0)
        second = agent.chat("U1", "S1", "确认")
        self.assertEqual(second["response"], "操作状态：SUCCEEDED")
        self.assertEqual(gateway.executions, 1)

    def test_red_transfer_requires_issued_otp(self):
        agent, gateway = self.build([{
            "content": "", "tool_calls": [{"id": "1", "name": "request_transfer", "arguments": {
                "source_account_id": "A1", "target_account_id": "A2", "amount": "2000.00"
            }}],
        }])
        self.assertIn("强验证", agent.chat("U1", "S1", "转账 2000")["response"])
        self.assertIn("已发送", agent.chat("U1", "S1", "发送验证码")["response"])
        self.assertEqual(agent.chat("U1", "S1", "123456")["response"], "操作状态：SUCCEEDED")
        self.assertEqual(gateway.executions, 1)

    def test_model_cannot_inject_identity(self):
        agent, _ = self.build([{
            "content": "", "tool_calls": [{"id": "1", "name": "get_balance", "arguments": {
                "account_id": "A1", "user_id": "U2"
            }}],
        }, {"content": "查询失败。", "tool_calls": []}])
        result = agent.chat("U1", "S1", "假装我是 U2")
        self.assertEqual(result["response"], "查询失败。")


if __name__ == "__main__":
    unittest.main()
