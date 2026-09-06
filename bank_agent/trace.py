import json
import re
from datetime import datetime, timezone
from pathlib import Path


SECRET_FIELDS = {"api_key", "authorization", "credential", "otp", "password", "token"}


class TraceLogger:
    """Append-only JSONL trace with basic secret and OTP redaction."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event, user_id, session_id, **details):
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "user_id": user_id,
            "session_id": session_id,
            "details": self.redact(details),
        }
        with self.path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    @classmethod
    def redact(cls, value, field=""):
        if field.lower() in SECRET_FIELDS:
            return "[REDACTED]"
        if isinstance(value, dict):
            return {key: cls.redact(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [cls.redact(item) for item in value]
        if isinstance(value, str):
            return re.sub(r"(?<!\d)\d{6}(?!\d)", "[REDACTED_OTP]", value)
        return value
