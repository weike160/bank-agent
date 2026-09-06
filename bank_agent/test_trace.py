import json
import tempfile
import unittest
from pathlib import Path

from bank_agent.trace import TraceLogger


class TraceLoggerTest(unittest.TestCase):
    def test_writes_jsonl_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.jsonl"
            trace = TraceLogger(path)
            trace.write(
                "tool_call", "U1", "S1",
                credential="123456", context={"content": "验证码是 123456"},
                arguments={"amount": "20.00"},
            )
            record = json.loads(path.read_text())
        self.assertEqual("[REDACTED]", record["details"]["credential"])
        self.assertEqual("验证码是 [REDACTED_OTP]", record["details"]["context"]["content"])
        self.assertEqual("20.00", record["details"]["arguments"]["amount"])


if __name__ == "__main__":
    unittest.main()
