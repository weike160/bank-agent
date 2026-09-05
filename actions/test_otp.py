import tempfile
import unittest
from pathlib import Path

from otp import OTPVerifier


class OTPVerifierTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sent = []
        self.verifier = OTPVerifier(
            lambda user_id, code: self.sent.append((user_id, code)),
            Path(self.temp_dir.name) / "otp.db",
            code_generator=lambda: "654321",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_code_is_bound_to_user_and_action(self):
        self.verifier.issue("U1", "ACT1")
        self.assertFalse(self.verifier.verify("U2", "ACT1", "654321"))
        self.assertFalse(self.verifier.verify("U1", "ACT2", "654321"))
        self.assertTrue(self.verifier.verify("U1", "ACT1", "654321"))

    def test_code_can_only_be_used_once(self):
        self.verifier.issue("U1", "ACT1")
        self.assertTrue(self.verifier.verify("U1", "ACT1", "654321"))
        self.assertFalse(self.verifier.verify("U1", "ACT1", "654321"))

    def test_code_expires(self):
        verifier = OTPVerifier(
            lambda user_id, code: None,
            Path(self.temp_dir.name) / "expired.db",
            ttl_seconds=-1,
            code_generator=lambda: "654321",
        )
        verifier.issue("U1", "ACT1")
        self.assertFalse(verifier.verify("U1", "ACT1", "654321"))

    def test_three_wrong_attempts_block_correct_code(self):
        self.verifier.issue("U1", "ACT1")
        for _ in range(3):
            self.assertFalse(self.verifier.verify("U1", "ACT1", "000000"))
        self.assertFalse(self.verifier.verify("U1", "ACT1", "654321"))

    def test_stored_value_is_not_plaintext_code(self):
        self.verifier.issue("U1", "ACT1")
        with self.verifier.connect() as db:
            row = db.execute(
                "SELECT code_hash FROM otp_challenges WHERE action_id = 'ACT1'"
            ).fetchone()
        self.assertNotEqual(b"654321", row["code_hash"])
        self.assertEqual([("U1", "654321")], self.sent)


if __name__ == "__main__":
    unittest.main()
