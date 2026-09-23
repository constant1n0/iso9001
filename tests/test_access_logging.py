"""Regression coverage for secret-safe Gunicorn access logging."""

import runpy
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gunicorn.glogging import Logger, SafeAtoms


CONFIG_PATH = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
LOG_TIME = "[23/Sep/2026:12:34:56 +0000]"


def load_access_log_format() -> str:
    """Load the access format from the real repository configuration."""
    config = runpy.run_path(str(CONFIG_PATH))
    return config["access_log_format"]


def build_access_atoms() -> dict[str, object]:
    """Build real Gunicorn atoms without constructing a file-backed logger."""
    logger = object.__new__(Logger)
    response = SimpleNamespace(
        status="204 No Content",
        sent=321,
        headers={},
    )
    request = SimpleNamespace(headers={})
    environ = {
        "REMOTE_ADDR": "192.0.2.44",
        "REQUEST_METHOD": "GET",
        "RAW_URI": "/raw-request-marker?raw-query-marker=value",
        "PATH_INFO": "/direct-path-marker",
        "QUERY_STRING": "next=direct-query-marker",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_REFERER": "https://client.example.invalid/referrer-marker",
        "HTTP_USER_AGENT": "A3-Test-Agent/1.0",
    }

    with patch.object(Logger, "now", return_value=LOG_TIME):
        return Logger.atoms(
            logger,
            response,
            request,
            environ,
            timedelta(seconds=1, microseconds=234_567),
        )


class AccessLoggingTestCase(unittest.TestCase):
    """Verify configured logging against Gunicorn's real atom behavior."""

    def setUp(self) -> None:
        self.access_log_format = load_access_log_format()
        self.atoms = build_access_atoms()
        self.rendered_log = self.access_log_format % SafeAtoms(self.atoms)

    def test_configured_format_omits_every_url_bearing_atom(self) -> None:
        forbidden_atoms = ("%(r)s", "%(U)s", "%(q)s", "%(f)s")

        for atom in forbidden_atoms:
            with self.subTest(atom=atom):
                self.assertNotIn(atom, self.access_log_format)

    def test_rendered_log_excludes_each_automatic_url_source(self) -> None:
        url_sources = {
            "raw-request-marker": self.atoms["r"],
            "direct-path-marker": self.atoms["U"],
            "direct-query-marker": self.atoms["q"],
            "referrer-marker": self.atoms["f"],
        }

        for marker, source_atom in url_sources.items():
            with self.subTest(marker=marker):
                self.assertIn(marker, source_atom)
                self.assertNotIn(marker, self.rendered_log)

    def test_rendered_log_retains_required_operational_metadata(self) -> None:
        retained_values = (
            "192.0.2.44",
            LOG_TIME,
            "204",
            "321",
            "A3-Test-Agent/1.0",
            "1234567",
        )

        for value in retained_values:
            with self.subTest(value=value):
                self.assertIn(value, self.rendered_log)


if __name__ == "__main__":
    unittest.main()
