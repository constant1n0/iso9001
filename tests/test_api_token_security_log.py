"""Security-log events for API tokens: no secrets, no log injection."""

from __future__ import annotations

import unittest

from flask import Flask


def log():
    from app.utils import security_logger

    return security_logger


class TokenSecurityLogTestCase(unittest.TestCase):
    def test_events_work_outside_a_request(self) -> None:
        with self.assertLogs("security", level="INFO") as logs:
            log().log_api_token_issued("a1b2c3d4", "ana", "cli:ops")
            log().log_api_token_revoked("a1b2c3d4", "cli:ops")
        self.assertEqual(2, len(logs.records))
        self.assertIn("API_TOKEN_ISSUED | prefix=a1b2c3d4 | user=ana | by=cli:ops | ip=-",
                      logs.output[0])
        self.assertIn("API_TOKEN_REVOKED | prefix=a1b2c3d4 | by=cli:ops", logs.output[1])

    def test_auth_failure_is_a_warning_with_reason_prefix_and_client_ip(self) -> None:
        app = Flask(__name__)
        with app.test_request_context(environ_base={"REMOTE_ADDR": "203.0.113.9"}):
            with self.assertLogs("security", level="INFO") as logs:
                log().log_api_token_auth_failed("bad_secret", "a1b2c3d4")
                log().log_api_token_auth_failed("malformed", None)
        self.assertEqual(["WARNING", "WARNING"], [r.levelname for r in logs.records])
        self.assertIn(
            "API_TOKEN_AUTH_FAILED | reason=bad_secret | prefix=a1b2c3d4 | ip=203.0.113.9",
            logs.output[0],
        )
        self.assertIn("prefix=- ", logs.output[1])

    def test_control_characters_cannot_forge_log_lines(self) -> None:
        with self.assertLogs("security", level="INFO") as logs:
            log().log_api_token_issued("a1b2c3d4", "ana\nAPI_TOKEN_REVOKED | x", "cli\r")
        (record,) = logs.records
        self.assertNotIn("\n", record.getMessage())
        self.assertNotIn("\r", record.getMessage())
        self.assertEqual(4, record.getMessage().count(" | "))


if __name__ == "__main__":
    unittest.main()
