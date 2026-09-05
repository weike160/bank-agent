import tempfile
import unittest
from pathlib import Path

from app import Bank


class BankTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.bank = Bank(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def execute_transfer(self, source, target, amount, note=""):
        prepared = self.bank.prepare_transfer(source, target, amount, note)
        return self.bank.execute_transfer(prepared["id"])

    def test_transfer_changes_balances_and_creates_transactions(self):
        self.execute_transfer("A1001", "A1002", "125.50", "rent")
        self.assertEqual("874.50", self.bank.account("A1001")["balance"])
        self.assertEqual("625.50", self.bank.account("A1002")["balance"])
        self.assertEqual("-125.50", self.bank.transactions("A1001")[0]["amount"])
        self.assertEqual("125.50", self.bank.transactions("A1002")[0]["amount"])
        self.assertEqual("transfer", self.bank.transactions("A1001")[0]["category"])

    def test_prepared_transfer_does_not_change_balance_until_executed(self):
        transfer = self.bank.prepare_transfer("A1001", "A1002", "125.50", "rent")
        self.assertEqual("prepared", transfer["status"])
        self.assertEqual("1000.00", self.bank.account("A1001")["balance"])
        self.assertEqual([], self.bank.transactions("A1001"))

        result = self.bank.execute_transfer(transfer["id"])
        self.assertEqual("executed", result["transfer"]["status"])
        self.assertEqual("874.50", result["from"]["balance"])
        self.assertEqual("-125.50", self.bank.transactions("A1001")[0]["amount"])

    def test_executing_same_transfer_twice_does_not_double_charge(self):
        transfer = self.bank.prepare_transfer("A1001", "A1002", "10.00")
        self.bank.execute_transfer(transfer["id"])
        self.bank.execute_transfer(transfer["id"])
        self.assertEqual("990.00", self.bank.account("A1001")["balance"])
        self.assertEqual(1, len(self.bank.transactions("A1001")))

    def test_freeze_changes_status_and_blocks_outgoing_money(self):
        self.assertEqual("frozen", self.bank.freeze("A1001")["status"])
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.execute_transfer("A1001", "A1002", "1.00")

    def test_card_status_and_limit_can_change(self):
        self.assertEqual("active", self.bank.cards()[0]["status"])
        self.assertEqual("frozen", self.bank.update_card_status("C1001", "frozen")["status"])
        self.assertEqual("active", self.bank.update_card_status("C1001", "active")["status"])
        self.assertEqual(
            "reported_lost",
            self.bank.update_card_status("C1001", "reported_lost")["status"],
        )
        self.assertEqual("3000.00", self.bank.set_card_limit("C1001", "3000.00")["limit"])

    def test_investment_changes_holding_and_balance(self):
        holdings = self.bank.invest("A1001", "稳健理财", "200.00")
        self.assertEqual([{"product": "稳健理财", "amount": "200.00"}], holdings)
        self.assertEqual("800.00", self.bank.account("A1001")["balance"])
        self.assertEqual("investment", self.bank.transactions("A1001")[0]["kind"])

    def test_investment_products_and_redeem(self):
        self.assertEqual(2, len(self.bank.investment_products()))
        self.bank.invest("A1001", "稳健理财", "200.00")
        holdings = self.bank.redeem("A1001", "稳健理财", "50.00")
        self.assertEqual([{"product": "稳健理财", "amount": "150.00"}], holdings)
        self.assertEqual("850.00", self.bank.account("A1001")["balance"])

    def test_cancel_debit_changes_status(self):
        self.assertEqual("cancelled", self.bank.cancel_debit("D1001")["status"])
        self.assertEqual("cancelled", self.bank.debit("D1001")["status"])

    def test_lists_accounts_payees_and_direct_debits(self):
        self.assertEqual(4, len(self.bank.accounts()))
        self.assertEqual("P1001", self.bank.payees()[0]["id"])
        self.assertEqual("99.00", self.bank.debits()[0]["amount"])

    def test_user_only_lists_owned_bank_resources(self):
        self.assertEqual(["A1001"], [item["id"] for item in self.bank.accounts("U1")])
        self.assertEqual(["A1002"], [item["id"] for item in self.bank.accounts("U2")])
        self.assertEqual(["C1001"], [item["id"] for item in self.bank.cards("U1")])
        self.assertEqual([], self.bank.cards("U2"))
        self.assertEqual(["P1001"], [item["id"] for item in self.bank.payees("U1")])
        self.assertEqual([], self.bank.debits("U2"))

    def test_new_users_have_independent_accounts(self):
        self.assertEqual(["A1003"], [item["id"] for item in self.bank.accounts("U3")])
        self.assertEqual(["A1004"], [item["id"] for item in self.bank.accounts("U4")])
        self.assertEqual("1000.00", self.bank.account("A1003")["balance"])
        self.assertEqual("1000.00", self.bank.account("A1004")["balance"])

    def test_data_survives_new_bank_instance(self):
        self.execute_transfer("A1001", "A1002", "10.00")
        restarted_bank = Bank(self.db_path)
        self.assertEqual("990.00", restarted_bank.account("A1001")["balance"])
        self.assertEqual(1, len(restarted_bank.transactions("A1001")))

    def test_invalid_transfer_rolls_back_everything(self):
        with self.assertRaisesRegex(ValueError, "insufficient"):
            self.execute_transfer("A1001", "A1002", "9999.00")
        self.assertEqual("1000.00", self.bank.account("A1001")["balance"])
        self.assertEqual([], self.bank.transactions("A1001"))


if __name__ == "__main__":
    unittest.main()
