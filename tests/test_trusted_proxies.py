"""The client address comes from ``X-Forwarded-*`` only behind a trusted proxy."""

from __future__ import annotations

import ipaddress
import unittest

from flask import request

import test_auth_bootstrap as bootstrap

from app.extensions import db
from app.utils.security_logger import get_client_ip
from app.utils.trusted_proxies import (
    ORIGINAL_ENVIRON_KEY,
    TrustedProxiesError,
    TrustedProxyMiddleware,
    parse_trusted_proxies,
)


TRAEFIK = "172.18.0.3"
TRAEFIK_NETWORK = "172.18.0.0/16"
OUTSIDER = "198.51.100.20"
CREDENTIALS = {"username": "nadie", "password": "incorrecta"}


class ParseTrustedProxiesTestCase(unittest.TestCase):
    def test_empty_values_trust_nobody(self) -> None:
        for value in (None, "", "   ", " , ,"):
            with self.subTest(value=value):
                self.assertEqual((), parse_trusted_proxies(value))

    def test_addresses_and_networks_are_accepted(self) -> None:
        self.assertEqual(
            (
                ipaddress.ip_network("127.0.0.1/32"),
                ipaddress.ip_network(TRAEFIK_NETWORK),
                ipaddress.ip_network("::1/128"),
                ipaddress.ip_network("fd00::/8"),
            ),
            parse_trusted_proxies(f" 127.0.0.1 ,{TRAEFIK_NETWORK}, ::1 , fd00::/8 "),
        )

    def test_a_list_is_accepted_too(self) -> None:
        self.assertEqual(
            (ipaddress.ip_network("10.0.0.5/32"),),
            parse_trusted_proxies(["10.0.0.5"]),
        )

    def test_a_wildcard_is_refused(self) -> None:
        for value in ("*", f"{TRAEFIK_NETWORK}, *", "0.0.0.0/0", "::/0"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TrustedProxiesError, "TRUSTED_PROXIES"):
                    parse_trusted_proxies(value)

    def test_an_invalid_entry_is_refused_and_named(self) -> None:
        invalid = ("localhost", "300.1.1.1", "10.0.0.0/33", "172.18.0.1/16", "[::1]")
        for entry in invalid:
            with self.subTest(entry=entry):
                with self.assertRaises(TrustedProxiesError) as raised:
                    parse_trusted_proxies(f"127.0.0.1, {entry}")
                self.assertIn("TRUSTED_PROXIES", str(raised.exception))
                self.assertIn(repr(entry), str(raised.exception))

    def test_the_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(TrustedProxiesError, ValueError))


class TrustedProxyMiddlewareTestCase(unittest.TestCase):
    def _call(self, trusted: str, **environ: str) -> dict:
        environ.setdefault("wsgi.url_scheme", "http")
        seen: dict = {}

        def inner(env, start_response):
            seen.update(env)
            return [b""]

        middleware = TrustedProxyMiddleware(inner, parse_trusted_proxies(trusted))
        middleware(environ, lambda *args: None)
        return seen

    def test_a_trusted_peer_sets_the_client_and_keeps_the_original(self) -> None:
        env = self._call(
            TRAEFIK_NETWORK, REMOTE_ADDR=TRAEFIK, HTTP_X_FORWARDED_FOR="203.0.113.7"
        )

        self.assertEqual("203.0.113.7", env["REMOTE_ADDR"])
        self.assertEqual(TRAEFIK, env[ORIGINAL_ENVIRON_KEY]["REMOTE_ADDR"])
        self.assertEqual("http", env[ORIGINAL_ENVIRON_KEY]["wsgi.url_scheme"])

    def test_an_untrusted_peer_changes_nothing(self) -> None:
        original = {
            "REMOTE_ADDR": OUTSIDER,
            "HTTP_X_FORWARDED_FOR": "1.2.3.4",
            "HTTP_X_FORWARDED_PROTO": "https",
            "wsgi.url_scheme": "http",
        }

        self.assertEqual(original, self._call(TRAEFIK_NETWORK, **original))

    def test_chained_proxies_resolve_to_the_right_most_untrusted_hop(self) -> None:
        env = self._call(
            f"{TRAEFIK_NETWORK}, 10.0.0.5",
            REMOTE_ADDR=TRAEFIK,
            HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.7, 10.0.0.5,172.18.0.9",
        )

        self.assertEqual("203.0.113.7", env["REMOTE_ADDR"])

    def test_an_all_trusted_chain_uses_the_left_most_hop(self) -> None:
        env = self._call(
            f"{TRAEFIK_NETWORK}, 10.0.0.5",
            REMOTE_ADDR=TRAEFIK,
            HTTP_X_FORWARDED_FOR="10.0.0.5, 172.18.0.9",
        )

        self.assertEqual("10.0.0.5", env["REMOTE_ADDR"])

    def test_a_missing_header_keeps_the_peer(self) -> None:
        env = self._call(TRAEFIK_NETWORK, REMOTE_ADDR=TRAEFIK)

        self.assertEqual(TRAEFIK, env["REMOTE_ADDR"])

    def test_garbage_entries_are_never_used(self) -> None:
        cases = {
            "not-an-ip": TRAEFIK,
            "": TRAEFIK,
            "1.2.3.4:5678": TRAEFIK,
            "garbage, 203.0.113.7": "203.0.113.7",
            # A malformed hop breaks the chain: nothing left of it is trusted.
            "1.2.3.4, bogus, 172.18.0.9": "172.18.0.9",
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                env = self._call(
                    TRAEFIK_NETWORK, REMOTE_ADDR=TRAEFIK, HTTP_X_FORWARDED_FOR=header
                )
                self.assertEqual(expected, env["REMOTE_ADDR"])

    def test_a_malformed_peer_changes_nothing(self) -> None:
        env = self._call(
            TRAEFIK_NETWORK, REMOTE_ADDR="unix-socket", HTTP_X_FORWARDED_FOR="1.2.3.4"
        )

        self.assertEqual("unix-socket", env["REMOTE_ADDR"])
        self.assertNotIn(ORIGINAL_ENVIRON_KEY, env)

    def test_ipv6_and_ipv4_mapped_peers_are_recognised(self) -> None:
        env = self._call(
            "fd00::/8", REMOTE_ADDR="fd00::1", HTTP_X_FORWARDED_FOR="2001:db8::7"
        )
        self.assertEqual("2001:db8::7", env["REMOTE_ADDR"])

        env = self._call(
            TRAEFIK_NETWORK,
            REMOTE_ADDR="::ffff:172.18.0.3",
            HTTP_X_FORWARDED_FOR="203.0.113.7",
        )
        self.assertEqual("203.0.113.7", env["REMOTE_ADDR"])

    def test_the_forwarded_proto_sets_the_scheme(self) -> None:
        cases = {"https": "https", " HTTPS ": "https", "http": "http", "ftp": "http"}
        for header, expected in cases.items():
            with self.subTest(header=header):
                env = self._call(
                    TRAEFIK_NETWORK,
                    REMOTE_ADDR=TRAEFIK,
                    HTTP_X_FORWARDED_PROTO=header,
                )
                self.assertEqual(expected, env["wsgi.url_scheme"])

    def test_an_empty_trust_list_is_a_programming_error(self) -> None:
        with self.assertRaises(ValueError):
            TrustedProxyMiddleware(lambda env, start_response: [], ())


class ClientAddressIntegrationTestCase(unittest.TestCase):
    @staticmethod
    def _login(client, peer: str, forwarded_for: str | None = None):
        headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
        return client.post(
            "/login",
            data=CREDENTIALS,
            headers=headers,
            environ_base={"REMOTE_ADDR": peer},
        )

    def test_the_request_sees_the_resolved_client_and_scheme(self) -> None:
        app = bootstrap.build_app_with_schema(self, TRUSTED_PROXIES=TRAEFIK_NETWORK)
        app.add_url_rule(
            "/_whoami", "whoami", lambda: f"{request.remote_addr} {request.scheme}"
        )

        response = app.test_client().get(
            "/_whoami",
            headers={
                "X-Forwarded-For": "1.2.3.4, 203.0.113.7",
                "X-Forwarded-Proto": "https",
            },
            environ_base={"REMOTE_ADDR": TRAEFIK},
        )

        self.assertEqual("203.0.113.7 https", response.get_data(as_text=True))

    def test_no_trusted_proxies_leaves_the_application_unwrapped(self) -> None:
        app = bootstrap.build_app_with_schema(self)

        self.assertNotIsInstance(app.wsgi_app, TrustedProxyMiddleware)

    def test_a_wildcard_stops_the_application_at_start_up(self) -> None:
        with self.assertRaisesRegex(TrustedProxiesError, r"TRUSTED_PROXIES.*'\*'"):
            bootstrap.build_app(TRUSTED_PROXIES="*")

    def test_the_security_log_records_the_resolved_client(self) -> None:
        app = bootstrap.build_app_with_schema(self, TRUSTED_PROXIES=TRAEFIK_NETWORK)

        with self.assertLogs("security", level="WARNING") as logs:
            self._login(app.test_client(), TRAEFIK, "1.2.3.4, 203.0.113.7")

        failed = [line for line in logs.output if "LOGIN_FAILED" in line]
        self.assertEqual(1, len(failed))
        self.assertIn("ip=203.0.113.7 ", failed[0])

    def test_clients_behind_the_same_proxy_keep_separate_login_budgets(self) -> None:
        app = bootstrap.build_app_with_schema(self, TRUSTED_PROXIES=TRAEFIK_NETWORK, RATELIMIT_ENABLED=True)
        client = app.test_client()

        first = [
            self._login(client, TRAEFIK, "203.0.113.7").status_code for _ in range(5)
        ]
        with self.assertLogs("security", level="WARNING") as logs:
            refused = self._login(client, TRAEFIK, "203.0.113.7")
        other = self._login(client, TRAEFIK, "203.0.113.8")

        self.assertEqual([302] * 5, first)
        self.assertEqual(429, refused.status_code)
        self.assertTrue(
            any("RATE_LIMIT_EXCEEDED" in line and "ip=203.0.113.7" in line
                for line in logs.output)
        )
        self.assertEqual(302, other.status_code)

    def test_a_spoofed_header_from_an_untrusted_peer_is_ignored(self) -> None:
        for trusted in ("", TRAEFIK_NETWORK):
            with self.subTest(trusted=trusted):
                app = bootstrap.build_app_with_schema(self, TRUSTED_PROXIES=trusted, RATELIMIT_ENABLED=True)
                client = app.test_client()

                with self.assertLogs("security", level="WARNING") as logs:
                    codes = [
                        self._login(client, OUTSIDER, f"1.2.3.{n}").status_code
                        for n in range(4, 10)
                    ]

                self.assertEqual([302] * 5 + [429], codes)
                self.assertTrue(all(f"ip={OUTSIDER}" in line for line in logs.output))
                self.assertFalse(any("1.2.3." in line for line in logs.output))


class GetClientIpTestCase(unittest.TestCase):
    def test_it_returns_the_resolved_address_never_the_raw_header(self) -> None:
        app = bootstrap.build_app()

        with app.test_request_context(
            "/",
            headers={"X-Forwarded-For": "1.2.3.4"},
            environ_base={"REMOTE_ADDR": OUTSIDER},
        ):
            self.assertEqual(OUTSIDER, get_client_ip())

    def test_it_still_needs_a_request(self) -> None:
        with self.assertRaises(RuntimeError):
            get_client_ip()


if __name__ == "__main__":
    unittest.main()
