import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "mock_bank"))

from app import Bank, Handler
from gateway import BankGateway, GatewayError


class BankGatewayTest(unittest.TestCase):
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
        database = Path(self.temp_dir.name) / f"{self._testMethodName}.db"
        Handler.bank = Bank(database)

    def test_get_balance_is_normalized(self):
        result = self.gateway.get_balance("A1001")
        self.assertEqual({
            "success": True,
            "account_id": "A1001",
            "balance": "1000.00",
            "currency": "CNY",
            "status": "active",
        }, result)

    def test_lists_accounts_and_payees(self):
        self.assertEqual(2, len(self.gateway.get_accounts()["accounts"]))
        self.assertEqual("P1001", self.gateway.get_payees()["payees"][0]["id"])
        self.assertEqual(["A1001"], [
            item["id"] for item in self.gateway.get_accounts("U1")["accounts"]
        ])
        self.assertEqual([], self.gateway.get_payees("U2")["payees"])

    def test_transfer_changes_balance_and_transactions(self):
        prepared = self.gateway.prepare_transfer("A1001", "A1002", "25.00", "gateway test")
        result = self.gateway.execute_transfer(prepared["transfer"]["id"])
        self.assertTrue(result["success"])
        self.assertEqual("975.00", self.gateway.get_balance("A1001")["balance"])
        transactions = self.gateway.get_transactions("A1001")["transactions"]
        self.assertEqual("-25.00", transactions[0]["amount"])

    def test_prepared_transfer_waits_for_execute(self):
        prepared = self.gateway.prepare_transfer("A1001", "A1002", "25.00")
        self.assertEqual("prepared", prepared["transfer"]["status"])
        self.assertEqual("1000.00", self.gateway.get_balance("A1001")["balance"])

        executed = self.gateway.execute_transfer(prepared["transfer"]["id"])
        self.assertEqual("executed", executed["transfer"]["status"])
        self.assertEqual("975.00", executed["source_account"]["balance"])

        repeated = self.gateway.execute_transfer(prepared["transfer"]["id"])
        self.assertEqual("975.00", repeated["source_account"]["balance"])
        stored = self.gateway.get_transfer(prepared["transfer"]["id"])
        self.assertEqual("executed", stored["transfer"]["status"])

    def test_freeze_account(self):
        result = self.gateway.freeze_account("A1002")
        self.assertEqual("frozen", result["account"]["status"])

    def test_card_operations(self):
        self.assertEqual("active", self.gateway.get_cards()["cards"][0]["status"])
        self.assertEqual("frozen", self.gateway.freeze_card("C1001")["card"]["status"])
        self.assertEqual("active", self.gateway.unfreeze_card("C1001")["card"]["status"])
        self.assertEqual(
            "reported_lost",
            self.gateway.report_card_lost("C1001")["card"]["status"],
        )
        self.assertEqual(
            "3000.00", self.gateway.set_card_limit("C1001", "3000.00")["card"]["limit"]
        )

    def test_purchase_investment_changes_holdings(self):
        result = self.gateway.purchase_investment("A1001", "稳健理财", "100.00")
        self.assertEqual(
            [{"product": "稳健理财", "amount": "100.00"}],
            result["holdings"],
        )
        self.assertEqual(result, self.gateway.get_holdings("A1001"))

    def test_investment_products_and_redeem(self):
        self.assertEqual(2, len(self.gateway.get_investment_products()["products"]))
        self.gateway.purchase_investment("A1001", "稳健理财", "200.00")
        result = self.gateway.redeem_investment("A1001", "稳健理财", "50.00")
        self.assertEqual("150.00", result["holdings"][0]["amount"])

    def test_cancel_direct_debit(self):
        result = self.gateway.cancel_direct_debit("D1001")
        self.assertEqual("cancelled", result["direct_debit"]["status"])
        self.assertEqual(result, self.gateway.get_direct_debit("D1001"))

    def test_lists_direct_debits(self):
        debits = self.gateway.get_direct_debits()["direct_debits"]
        self.assertEqual("99.00", debits[0]["amount"])
        self.assertEqual([], self.gateway.get_direct_debits("U2")["direct_debits"])

    def test_bank_error_becomes_gateway_error(self):
        with self.assertRaises(GatewayError) as context:
            self.gateway.get_balance("missing")
        self.assertEqual(404, context.exception.status)
        self.assertEqual("account not found", str(context.exception))

    def test_direct_transfer_bypass_does_not_exist(self):
        self.assertFalse(hasattr(self.gateway, "transfer"))
        with self.assertRaises(GatewayError) as context:
            self.gateway.request("POST", "/transfers", {
                "from": "A1001", "to": "A1002", "amount": "1.00"
            })
        self.assertEqual(404, context.exception.status)


if __name__ == "__main__":
    unittest.main()
