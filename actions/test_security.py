import tempfile
import unittest
from pathlib import Path

from security import (
    ActionError,
    ActionService,
    PermissionPolicy,
    RiskLevel,
    VerificationError,
)
from otp import OTPVerifier


class ActionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.executions = []
        self.sent_codes = {}
        self.policy = PermissionPolicy()
        verifier = OTPVerifier(
            lambda user_id, code: self.sent_codes.__setitem__(user_id, code),
            Path(self.temp_dir.name) / "otp.db",
            code_generator=lambda: "123456",
        )
        self.service = ActionService(
            self.policy,
            Path(self.temp_dir.name) / "actions.db",
            verifier=verifier,
            context_provider=lambda action_type, user_id, payload: {"daily_total": 600},
        )
        self.session_id = self.service.create_session("U1", "S1")["session_id"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def register(self, action_type, level_or_rule):
        self.policy.register(action_type, level_or_rule)
        self.service.register(
            action_type,
            lambda payload: self.executions.append(payload) or {"accepted": True},
        )

    def test_green_action_executes_automatically(self):
        self.register("READ", RiskLevel.GREEN)
        action = self.service.submit("READ", {"account_id": "A1"}, "U1")
        self.assertEqual("SUCCEEDED", action["status"])
        self.assertEqual([{"account_id": "A1"}], self.executions)

    def test_yellow_action_waits_for_confirmation(self):
        self.register("CANCEL", RiskLevel.YELLOW)
        action = self.service.submit(
            "CANCEL", {"debit_id": "D1"}, "U1", session_id=self.session_id
        )
        self.assertEqual("WAITING_CONFIRMATION", action["status"])
        self.assertEqual([], self.executions)

        action = self.service.confirm(action["action_id"], "U1")
        self.assertEqual("SUCCEEDED", action["status"])
        self.assertEqual(1, len(self.executions))
        with self.assertRaises(ActionError):
            self.service.confirm(action["action_id"], "U1")

    def test_red_action_requires_strong_verification(self):
        self.register("INVEST", RiskLevel.RED)
        action = self.service.submit(
            "INVEST", {"amount": "100.00"}, "U1", session_id=self.session_id
        )
        self.assertEqual("WAITING_STRONG_AUTH", action["status"])

        challenge = self.service.request_verification(action["action_id"], "U1")
        self.assertEqual(action["action_id"], challenge["action_id"])
        self.assertEqual("123456", self.sent_codes["U1"])
        action = self.service.verify(action["action_id"], "U1", "123456")
        self.assertEqual("SUCCEEDED", action["status"])
        self.assertEqual(1, len(self.executions))

    def test_three_failed_verifications_block_action(self):
        self.register("INVEST", RiskLevel.RED)
        action = self.service.submit(
            "INVEST", {"amount": "100.00"}, "U1", session_id=self.session_id
        )
        self.service.request_verification(action["action_id"], "U1")
        for _ in range(3):
            with self.assertRaises(VerificationError):
                self.service.verify(action["action_id"], "U1", "wrong")
        action = self.service.get_action(action["action_id"])
        self.assertEqual("BLOCKED", action["status"])
        self.assertEqual(3, action["verification_failures"])

    def test_dynamic_rule_can_use_trusted_context(self):
        def transfer_risk(payload, context):
            total = context["daily_total"] + int(payload["amount"])
            return RiskLevel.YELLOW if total <= 1000 else RiskLevel.RED

        self.register("TRANSFER", transfer_risk)
        small = self.service.submit(
            "TRANSFER", {"amount": 300}, "U1", session_id=self.session_id
        )
        second_session = self.service.create_session("U1", "S2")["session_id"]
        large = self.service.submit(
            "TRANSFER", {"amount": 500}, "U1", session_id=second_session
        )
        self.assertEqual("YELLOW", small["risk_level"])
        self.assertEqual("RED", large["risk_level"])

    def test_idempotency_key_returns_same_action(self):
        self.register("READ", RiskLevel.GREEN)
        first = self.service.submit("READ", {}, "U1", idempotency_key="request-1")
        second = self.service.submit("READ", {}, "U1", idempotency_key="request-1")
        self.assertEqual(first["action_id"], second["action_id"])
        self.assertEqual(1, len(self.executions))

    def test_action_and_audit_survive_restart(self):
        self.register("READ", RiskLevel.GREEN)
        action = self.service.submit("READ", {}, "U1")
        restarted = ActionService(self.policy, self.service.db_path)
        self.assertEqual("SUCCEEDED", restarted.get_action(action["action_id"])["status"])
        self.assertEqual(3, len(restarted.get_audit_logs(action["action_id"])))

    def test_missing_policy_is_denied(self):
        self.service.register("UNKNOWN", lambda payload: {})
        with self.assertRaisesRegex(ActionError, "no permission rule"):
            self.service.submit("UNKNOWN", {}, "U1")

    def test_session_finds_and_confirms_pending_action(self):
        self.register("CANCEL", RiskLevel.YELLOW)
        action = self.service.submit(
            "CANCEL", {"debit_id": "D1"}, "U1", session_id=self.session_id
        )
        pending = self.service.get_pending_action("U1", self.session_id)
        self.assertEqual(action["action_id"], pending["action_id"])
        confirmed = self.service.confirm_pending("U1", self.session_id)
        self.assertEqual("SUCCEEDED", confirmed["status"])
        self.assertIsNone(self.service.get_pending_action("U1", self.session_id))

    def test_session_cannot_be_used_by_another_user(self):
        self.register("CANCEL", RiskLevel.YELLOW)
        with self.assertRaisesRegex(LookupError, "session not found"):
            self.service.submit(
                "CANCEL", {"debit_id": "D1"}, "U2", session_id=self.session_id
            )

    def test_session_only_allows_one_pending_action(self):
        self.register("CANCEL", RiskLevel.YELLOW)
        self.service.submit(
            "CANCEL", {"debit_id": "D1"}, "U1", session_id=self.session_id
        )
        with self.assertRaisesRegex(ActionError, "pending action"):
            self.service.submit(
                "CANCEL", {"debit_id": "D2"}, "U1", session_id=self.session_id
            )

    def test_executor_failure_is_recorded_not_invented_as_success(self):
        self.policy.register("FAIL", RiskLevel.GREEN)
        self.service.register("FAIL", lambda payload: (_ for _ in ()).throw(RuntimeError("bank down")))
        action = self.service.submit("FAIL", {}, "U1")
        self.assertEqual("FAILED", action["status"])
        self.assertEqual("bank down", action["error"])
        self.assertEqual(["CREATED", "EXECUTING", "FAILED"], [
            item["event"] for item in self.service.get_audit_logs(action["action_id"])
        ])


if __name__ == "__main__":
    unittest.main()
