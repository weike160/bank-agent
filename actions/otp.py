import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_DB = Path(__file__).with_name("otp.db")


def utc_now():
    return datetime.now(timezone.utc)


class OTPVerifier:
    """One-time verification codes bound to one user and one action."""

    def __init__(self, sender, db_path=DEFAULT_DB, ttl_seconds=300, max_attempts=3, code_generator=None):
        self.sender = sender
        self.db_path = str(db_path)
        self.ttl_seconds = ttl_seconds
        self.max_attempts = max_attempts
        self.code_generator = code_generator or self.generate_code
        self.setup()

    @staticmethod
    def generate_code():
        return f"{secrets.randbelow(1_000_000):06d}"

    @staticmethod
    def hash_code(code, salt):
        return hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 100_000)

    def connect(self):
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def setup(self):
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS otp_challenges (
                    action_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    code_hash BLOB NOT NULL,
                    salt BLOB NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
            """)

    def issue(self, user_id, action_id):
        code = self.code_generator()
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            raise ValueError("verification code generator must return 6 digits")
        salt = secrets.token_bytes(16)
        expires_at = utc_now() + timedelta(seconds=self.ttl_seconds)
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO otp_challenges "
                "(action_id, user_id, code_hash, salt, expires_at) VALUES (?, ?, ?, ?, ?)",
                (action_id, user_id, self.hash_code(code, salt), salt, expires_at.isoformat()),
            )
        try:
            self.sender(user_id, code)
        except Exception:
            with self.connect() as db:
                db.execute("DELETE FROM otp_challenges WHERE action_id = ?", (action_id,))
            raise
        return {"action_id": action_id, "expires_at": expires_at.isoformat()}

    def verify(self, user_id, action_id, credential):
        with self.connect() as db:
            challenge = db.execute(
                "SELECT * FROM otp_challenges WHERE action_id = ? AND user_id = ?",
                (action_id, user_id),
            ).fetchone()
            if not challenge or challenge["consumed_at"]:
                return False
            if datetime.fromisoformat(challenge["expires_at"]) <= utc_now():
                return False
            if challenge["attempts"] >= self.max_attempts:
                return False

            actual_hash = self.hash_code(str(credential), challenge["salt"])
            if not hmac.compare_digest(actual_hash, challenge["code_hash"]):
                db.execute(
                    "UPDATE otp_challenges SET attempts = attempts + 1 WHERE action_id = ?",
                    (action_id,),
                )
                return False

            db.execute(
                "UPDATE otp_challenges SET consumed_at = ? WHERE action_id = ?",
                (utc_now().isoformat(), action_id),
            )
            return True
