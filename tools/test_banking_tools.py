import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mock_bank"))

from app import Bank, Handler
from actions import OTPVerifier
from bank_gateway import BankGateway
from tools import BankingTools, create_bank_action_service, create_banking_tool_registry


class BankingToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.gateway = BankGateway(f"http://{host}:{port}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp_dir.cleanup()

    def setUp(self):
        test_dir = Path(self.temp_dir.name)
        Handler.bank = Bank(test_dir / f"{self._testMethodName}-bank.db")
        self.sent_codes = {}
        verifier = OTPVerifier(
            lambda user_id, code: self.sent_codes.__setitem__(user_id, code),
            test_dir / f"{self._testMethodName}-otp.db",
            code_generator=lambda: "123456",
        )
        actions = create_bank_action_service(
            self.gateway,
            test_dir / f"{self._testMethodName}-actions.db",
            verifier=verifier,
        )
        actions.create_session("U1", "S1")
        actions.create_session("U1", "S2")
        actions.create_session("U2", "S3")
        self.tools = BankingTools(self.gateway, actions)
        self.registry = create_banking_tool_registry(self.tools)

    def test_read_tools_use_real_bank_data(self):
        self.assertEqual("1000.00", self.tools.get_balance("U1", "A1001")["data"]["balance"])
        self.assertEqual("P1001", self.tools.find_payee("U1", "13800000000")["data"][0]["id"])
        self.assertEqual(2, len(self.tools.get_investment_products()["data"]))
        self.assertEqual("D1001", self.tools.get_direct_debits("U1")["data"][0]["id"])

    def test_yellow_transfer_waits_for_confirmation(self):
        requested = self.tools.request_transfer(
            "U1", "S1", "A1001", "A1002", "100.00", idempotency_key="transfer-1"
        )
        action = requested["data"]
        self.assertEqual("YELLOW", action["risk_level"])
        self.assertEqual("WAITING_CONFIRMATION", action["status"])
        self.assertEqual("1000.00", self.tools.get_balance("U1", "A1001")["data"]["balance"])

        confirmed = self.tools.confirm_pending_action("U1", "S1")["data"]
        self.assertEqual("SUCCEEDED", confirmed["status"])
        self.assertEqual("900.00", self.tools.get_balance("U1", "A1001")["data"]["balance"])

    def test_red_investment_requires_strong_verification(self):
        requested = self.tools.request_purchase_investment(
            "U1", "S1", "A1001", "稳健理财", "200.00"
        )
        action = requested["data"]
        self.assertEqual("RED", action["risk_level"])
        self.assertEqual("WAITING_STRONG_AUTH", action["status"])
        self.assertEqual([], self.tools.get_investment_positions("U1", "A1001")["data"])

        challenge = self.tools.request_pending_verification("U1", "S1")["data"]
        self.assertEqual(action["action_id"], challenge["action_id"])
        self.assertEqual("123456", self.sent_codes["U1"])
        verified = self.tools.verify_pending_action("U1", "S1", "123456")["data"]
        self.assertEqual("SUCCEEDED", verified["status"])
        self.assertEqual("200.00", self.tools.get_investment_positions("U1", "A1001")["data"][0]["amount"])

    def test_card_and_debit_requests_do_not_execute_before_confirmation(self):
        freeze = self.tools.request_freeze_card("U1", "S1", "C1001")["data"]
        cancel = self.tools.request_cancel_direct_debit("U1", "S2", "D1001")["data"]
        self.assertEqual("WAITING_CONFIRMATION", freeze["status"])
        self.assertEqual("WAITING_CONFIRMATION", cancel["status"])
        self.assertEqual("active", self.tools.get_cards("U1")["data"][0]["status"])
        self.assertEqual("active", self.tools.get_direct_debits("U1")["data"][0]["status"])

    def test_analysis_uses_transactions_and_does_not_count_transfers_as_spending(self):
        prepared = self.gateway.prepare_transfer("A1001", "A1002", "50.00")
        self.gateway.execute_transfer(prepared["transfer"]["id"])
        analysis = self.tools.analyze_spending("U1", "A1001")["data"]
        unusual = self.tools.detect_unusual_transactions("U1", "A1001", "50.00")["data"]
        self.assertEqual("0.00", analysis["total_expense"])
        self.assertEqual(1, len(unusual))

    def test_gateway_error_is_returned_not_invented_as_success(self):
        result = self.tools.get_balance("U1", "missing")
        self.assertFalse(result["success"])
        self.assertIsNone(result["data"])
        self.assertEqual("account does not belong to user", result["error"])

    def test_user_cannot_access_another_users_account(self):
        result = self.tools.get_balance("U2", "A1001")
        self.assertFalse(result["success"])
        requested = self.tools.request_transfer("U2", "S3", "A1001", "A1002", "10.00")
        self.assertFalse(requested["success"])
        self.assertEqual("account does not belong to user", requested["error"])

    def test_registry_injects_identity_and_validates_schema(self):
        names = [item["function"]["name"] for item in self.registry.definitions()]
        self.assertIn("get_balance", names)
        self.assertNotIn("execute_transfer", names)
        self.assertNotIn("verify_pending_action", names)
        definition = next(
            item for item in self.registry.definitions()
            if item["function"]["name"] == "get_balance"
        )
        self.assertNotIn("user_id", definition["function"]["parameters"]["properties"])

        result = self.registry.call("get_balance", {"account_id": "A1001"}, "U1", "S1")
        self.assertEqual("1000.00", result["data"]["balance"])
        invalid = self.registry.call(
            "get_balance", {"account_id": "A1001", "user_id": "U2"}, "U1", "S1"
        )
        self.assertFalse(invalid["success"])
        self.assertIn("unknown fields", invalid["error"])


if __name__ == "__main__":
    unittest.main()
