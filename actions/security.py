import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path


DEFAULT_DB = Path(__file__).with_name("actions.db")


class RiskLevel(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class ActionStatus(str, Enum):
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_STRONG_AUTH = "WAITING_STRONG_AUTH"
    CONFIRMED = "CONFIRMED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"


class ActionError(Exception):
    pass


class VerificationError(ActionError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


class PermissionPolicy:
    """Reusable fail-closed red/yellow/green permission rules."""

    def __init__(self):
        self.rules = {}

    def register(self, action_type, level_or_rule):
        self.rules[action_type] = level_or_rule

    def evaluate(self, action_type, payload, context=None):
        if action_type not in self.rules:
            raise ActionError(f"no permission rule for {action_type}")
        rule = self.rules[action_type]
        level = rule(payload, context or {}) if callable(rule) else rule
        try:
            return RiskLevel(level)
        except ValueError as error:
            raise ActionError(f"invalid risk level for {action_type}") from error


class ActionService:
    def __init__(
        self,
        policy,
        db_path=DEFAULT_DB,
        verifier=None,
        context_provider=None,
        max_verification_failures=3,
    ):
        self.policy = policy
        self.db_path = str(db_path)
        self.verifier = verifier
        self.context_provider = context_provider or (lambda action_type, user_id, payload: {})
        self.max_verification_failures = max_verification_failures
        self.executors = {}
        self.validators = {}
        self.setup()

    def connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def setup(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    action_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT,
                    verification_failures INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    confirmed_at TEXT,
                    executed_at TEXT,
                    result TEXT,
                    error TEXT,
                    UNIQUE(user_id, action_type, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL REFERENCES actions(id),
                    event TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_action_per_session
                ON actions(user_id, session_id)
                WHERE status IN ('WAITING_CONFIRMATION', 'WAITING_STRONG_AUTH');
            """)

    def register(self, action_type, executor, validator=None):
        self.executors[action_type] = executor
        if validator:
            self.validators[action_type] = validator

    def create_session(self, user_id, session_id=None):
        if not user_id:
            raise ActionError("user_id is required")
        session_id = session_id or "SES" + uuid.uuid4().hex[:12].upper()
        created_at = utc_now().isoformat()
        with self.connect() as db:
            existing = db.execute(
                "SELECT user_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if existing and existing["user_id"] != user_id:
                raise ActionError("session belongs to another user")
            db.execute(
                "INSERT OR IGNORE INTO sessions VALUES (?, ?, ?, ?)",
                (session_id, user_id, created_at, created_at),
            )
        return {"session_id": session_id, "user_id": user_id}

    def get_session(self, session_id, user_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
            ).fetchone()
        if not row:
            raise LookupError("session not found")
        return dict(row)

    def submit(
        self,
        action_type,
        payload,
        user_id,
        session_id=None,
        idempotency_key=None,
        expires_in_seconds=600,
    ):
        if action_type not in self.executors:
            raise ActionError(f"no executor for {action_type}")
        if not isinstance(payload, dict):
            raise ActionError("payload must be an object")
        if action_type in self.validators:
            self.validators[action_type](payload)

        if idempotency_key:
            existing = self.find_by_idempotency(user_id, action_type, idempotency_key)
            if existing:
                return existing

        context = self.context_provider(action_type, user_id, payload)
        risk_level = self.policy.evaluate(action_type, payload, context)
        status = {
            RiskLevel.GREEN: ActionStatus.EXECUTING,
            RiskLevel.YELLOW: ActionStatus.WAITING_CONFIRMATION,
            RiskLevel.RED: ActionStatus.WAITING_STRONG_AUTH,
        }[risk_level]
        if session_id:
            self.get_session(session_id, user_id)
        if risk_level != RiskLevel.GREEN:
            if not session_id:
                raise ActionError("session_id is required for protected actions")
            if self.get_pending_action(user_id, session_id):
                raise ActionError("session already has a pending action")
        created_at = utc_now()
        action_id = "ACT" + uuid.uuid4().hex[:12].upper()
        with self.connect() as db:
            db.execute(
                "INSERT INTO actions "
                "(id, user_id, session_id, action_type, payload, risk_level, status, "
                "idempotency_key, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    action_id,
                    user_id,
                    session_id,
                    action_type,
                    json.dumps(payload, ensure_ascii=False),
                    risk_level.value,
                    status.value,
                    idempotency_key,
                    created_at.isoformat(),
                    (created_at + timedelta(seconds=expires_in_seconds)).isoformat(),
                ),
            )
        self.audit(action_id, "CREATED", {"risk_level": risk_level.value, "status": status.value})
        if risk_level == RiskLevel.GREEN:
            return self._execute(action_id)
        return self.get_action(action_id)

    def get_pending_action(self, user_id, session_id):
        self.get_session(session_id, user_id)
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM actions WHERE user_id = ? AND session_id = ? "
                "AND status IN (?, ?) ORDER BY created_at DESC LIMIT 1",
                (
                    user_id,
                    session_id,
                    ActionStatus.WAITING_CONFIRMATION.value,
                    ActionStatus.WAITING_STRONG_AUTH.value,
                ),
            ).fetchone()
        return self.action_json(row) if row else None

    def confirm_pending(self, user_id, session_id):
        action = self.get_pending_action(user_id, session_id)
        if not action:
            raise LookupError("pending action not found")
        return self.confirm(action["action_id"], user_id)

    def verify_pending(self, user_id, session_id, credential):
        action = self.get_pending_action(user_id, session_id)
        if not action:
            raise LookupError("pending action not found")
        return self.verify(action["action_id"], user_id, credential)

    def request_pending_verification(self, user_id, session_id):
        action = self.get_pending_action(user_id, session_id)
        if not action:
            raise LookupError("pending action not found")
        return self.request_verification(action["action_id"], user_id)

    def reject_pending(self, user_id, session_id):
        action = self.get_pending_action(user_id, session_id)
        if not action:
            raise LookupError("pending action not found")
        return self.reject(action["action_id"], user_id)

    def confirm(self, action_id, user_id):
        action = self._load_for_user(action_id, user_id)
        self._expire_if_needed(action)
        if action["risk_level"] != RiskLevel.YELLOW.value:
            raise ActionError("action does not use confirmation")
        if action["status"] != ActionStatus.WAITING_CONFIRMATION.value:
            raise ActionError(f'action cannot be confirmed from {action["status"]}')
        with self.connect() as db:
            changed = db.execute(
                "UPDATE actions SET status = ?, confirmed_at = ? "
                "WHERE id = ? AND status = ?",
                (
                    ActionStatus.CONFIRMED.value,
                    utc_now().isoformat(),
                    action_id,
                    ActionStatus.WAITING_CONFIRMATION.value,
                ),
            ).rowcount
        if not changed:
            raise ActionError("action status changed before confirmation")
        self.audit(action_id, "CONFIRMED", {})
        return self._execute(action_id)

    def verify(self, action_id, user_id, credential):
        action = self._load_for_user(action_id, user_id)
        self._expire_if_needed(action)
        if action["risk_level"] != RiskLevel.RED.value:
            raise ActionError("action does not use strong verification")
        if action["status"] != ActionStatus.WAITING_STRONG_AUTH.value:
            raise ActionError(f'action cannot be verified from {action["status"]}')
        if not self.verifier or not hasattr(self.verifier, "verify"):
            raise ActionError("strong verifier is not configured")

        if not self.verifier.verify(user_id, action_id, credential):
            failures = action["verification_failures"] + 1
            status = (
                ActionStatus.BLOCKED
                if failures >= self.max_verification_failures
                else ActionStatus.WAITING_STRONG_AUTH
            )
            with self.connect() as db:
                db.execute(
                    "UPDATE actions SET verification_failures = ?, status = ? WHERE id = ?",
                    (failures, status.value, action_id),
                )
            self.audit(action_id, "VERIFICATION_FAILED", {"failures": failures})
            raise VerificationError("strong verification failed")

        with self.connect() as db:
            changed = db.execute(
                "UPDATE actions SET status = ?, confirmed_at = ? "
                "WHERE id = ? AND status = ?",
                (
                    ActionStatus.CONFIRMED.value,
                    utc_now().isoformat(),
                    action_id,
                    ActionStatus.WAITING_STRONG_AUTH.value,
                ),
            ).rowcount
        if not changed:
            raise ActionError("action status changed before verification")
        self.audit(action_id, "VERIFIED", {})
        return self._execute(action_id)

    def request_verification(self, action_id, user_id):
        action = self._load_for_user(action_id, user_id)
        self._expire_if_needed(action)
        if action["risk_level"] != RiskLevel.RED.value:
            raise ActionError("action does not use strong verification")
        if action["status"] != ActionStatus.WAITING_STRONG_AUTH.value:
            raise ActionError(f'action cannot request verification from {action["status"]}')
        if not self.verifier or not hasattr(self.verifier, "issue"):
            raise ActionError("strong verifier is not configured")
        challenge = self.verifier.issue(user_id, action_id)
        self.audit(action_id, "VERIFICATION_ISSUED", {"expires_at": challenge["expires_at"]})
        return challenge

    def reject(self, action_id, user_id):
        action = self._load_for_user(action_id, user_id)
        if action["status"] not in {
            ActionStatus.WAITING_CONFIRMATION.value,
            ActionStatus.WAITING_STRONG_AUTH.value,
        }:
            raise ActionError(f'action cannot be rejected from {action["status"]}')
        with self.connect() as db:
            changed = db.execute(
                "UPDATE actions SET status = ? WHERE id = ? AND status = ?",
                (ActionStatus.REJECTED.value, action_id, action["status"]),
            ).rowcount
        if not changed:
            raise ActionError("action status changed before rejection")
        self.audit(action_id, "REJECTED", {})
        return self.get_action(action_id)

    def get_action(self, action_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if not row:
            raise LookupError("action not found")
        return self.action_json(row)

    def get_audit_logs(self, action_id):
        self.get_action(action_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT event, details, created_at FROM audit_logs "
                "WHERE action_id = ? ORDER BY id",
                (action_id,),
            ).fetchall()
        return [{
            "event": row["event"],
            "details": json.loads(row["details"]),
            "created_at": row["created_at"],
        } for row in rows]

    def find_by_idempotency(self, user_id, action_type, idempotency_key):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM actions WHERE user_id = ? AND action_type = ? "
                "AND idempotency_key = ?",
                (user_id, action_type, idempotency_key),
            ).fetchone()
        return self.action_json(row) if row else None

    def audit(self, action_id, event, details):
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_logs (action_id, event, details, created_at) "
                "VALUES (?, ?, ?, ?)",
                (action_id, event, json.dumps(details, ensure_ascii=False), utc_now().isoformat()),
            )

    def _load_for_user(self, action_id, user_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM actions WHERE id = ? AND user_id = ?", (action_id, user_id)
            ).fetchone()
        if not row:
            raise LookupError("action not found")
        return row

    def _expire_if_needed(self, action):
        if datetime.fromisoformat(action["expires_at"]) > utc_now():
            return
        with self.connect() as db:
            db.execute(
                "UPDATE actions SET status = ? WHERE id = ?",
                (ActionStatus.EXPIRED.value, action["id"]),
            )
        self.audit(action["id"], "EXPIRED", {})
        raise ActionError("action expired")

    def _execute(self, action_id):
        action = self.get_action(action_id)
        if action["status"] != ActionStatus.EXECUTING.value:
            with self.connect() as db:
                db.execute(
                    "UPDATE actions SET status = ? WHERE id = ?",
                    (ActionStatus.EXECUTING.value, action_id),
                )
        self.audit(action_id, "EXECUTING", {})
        try:
            result = self.executors[action["action_type"]](action["payload"])
            result_json = json.dumps(result, ensure_ascii=False)
            status = ActionStatus.SUCCEEDED
            error = None
        except Exception as execution_error:
            result_json = None
            status = ActionStatus.FAILED
            error = str(execution_error)
        with self.connect() as db:
            db.execute(
                "UPDATE actions SET status = ?, executed_at = ?, result = ?, error = ? WHERE id = ?",
                (status.value, utc_now().isoformat(), result_json, error, action_id),
            )
        self.audit(action_id, status.value, {"error": error} if error else {})
        return self.get_action(action_id)

    @staticmethod
    def action_json(row):
        return {
            "action_id": row["id"],
            "user_id": row["user_id"],
            "session_id": row["session_id"],
            "action_type": row["action_type"],
            "payload": json.loads(row["payload"]),
            "risk_level": row["risk_level"],
            "status": row["status"],
            "idempotency_key": row["idempotency_key"],
            "verification_failures": row["verification_failures"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "confirmed_at": row["confirmed_at"],
            "executed_at": row["executed_at"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
        }
