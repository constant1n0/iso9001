"""Gunicorn leaves proxy headers to the app's TRUSTED_PROXIES middleware."""

from __future__ import annotations

import runpy
import unittest
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"


class GunicornProxyTrustTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = runpy.run_path(str(CONFIG_PATH))

    def test_forwarded_headers_are_not_trusted_from_any_peer(self) -> None:
        # Gunicorn's default trusts only loopback; the app resolves the scheme
        # and the client address for the networks listed in TRUSTED_PROXIES.
        self.assertNotIn("*", str(self.config.get("forwarded_allow_ips", "")))

    def test_proxy_protocol_is_not_trusted_from_any_peer(self) -> None:
        self.assertNotIn("*", str(self.config.get("proxy_allow_ips", "")))


if __name__ == "__main__":
    unittest.main()
