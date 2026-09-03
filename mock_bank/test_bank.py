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

    def test_transfer_changes_balances_and_creates_transactions(self):
        self.bank.transfer("A1001", "A1002", "125.50", "rent")
        self.assertEqual("874.50", self.bank.account("A1001")["balance"])
        self.assertEqual("625.50", self.bank.account("A1002")["balance"])
        self.assertEqual("-125.50", self.bank.transactions("A1001")[0]["amount"])
        self.assertEqual("125.50", self.bank.transactions("A1002")[0]["amount"])

    def test_freeze_changes_status_and_blocks_outgoing_money(self):
        self.assertEqual("frozen", self.bank.freeze("A1001")["status"])
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.bank.transfer("A1001", "A1002", "1.00")

    def test_investment_changes_holding_and_balance(self):
        holdings = self.bank.invest("A1001", "稳健理财", "200.00")
        self.assertEqual([{"product": "稳健理财", "amount": "200.00"}], holdings)
        self.assertEqual("800.00", self.bank.account("A1001")["balance"])
        self.assertEqual("investment", self.bank.transactions("A1001")[0]["kind"])

    def test_cancel_debit_changes_status(self):
        self.assertEqual("cancelled", self.bank.cancel_debit("D1001")["status"])
        self.assertEqual("cancelled", self.bank.debit("D1001")["status"])

    def test_data_survives_new_bank_instance(self):
        self.bank.transfer("A1001", "A1002", "10.00")
        restarted_bank = Bank(self.db_path)
        self.assertEqual("990.00", restarted_bank.account("A1001")["balance"])
        self.assertEqual(1, len(restarted_bank.transactions("A1001")))

    def test_invalid_transfer_rolls_back_everything(self):
        with self.assertRaisesRegex(ValueError, "insufficient"):
            self.bank.transfer("A1001", "A1002", "9999.00")
        self.assertEqual("1000.00", self.bank.account("A1001")["balance"])
        self.assertEqual([], self.bank.transactions("A1001"))


if __name__ == "__main__":
    unittest.main()
