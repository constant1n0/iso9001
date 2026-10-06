"""Rate limits for the MCP HTTP transport.

Two limits, counted with the ``limits`` library on the web application's
rate-limit storage (``RATELIMIT_STORAGE_URI``: Redis in production, memory
otherwise, which is consistent because the MCP server is one process):

- requests per API token, after authentication (``MCP_TOKEN_RATE_LIMIT``);
- failed bearer authentications per client address
  (``MCP_AUTH_FAILURE_RATE_LIMIT``), counted after the lookup with one atomic
  hit, so only failing requests are refused and a valid token never is.

A storage failure never blocks a request: the limiter fails open, logging one
warning when the storage stops answering and one info line when it is back
(the error type only, never the storage URI). A Redis storage without its own
socket timeouts gets one-second ones, so a stalled Redis cannot hold a request
for the TCP timeout. The stdio transport has no limits: it runs locally as one
configured token, so whoever can start it already has access to the server.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from flask import Flask
from urllib.parse import unquote, urlsplit, urlunsplit

from limits import RateLimitItem, parse_many
from limits.errors import ConfigurationError, StorageError
from limits.storage import MovingWindowSupport, Storage, storage_from_string
from limits.strategies import FixedWindowRateLimiter, MovingWindowRateLimiter, RateLimiter

logger = logging.getLogger(__name__)

TOKEN_LIMIT_SETTING = "MCP_TOKEN_RATE_LIMIT"
FAILURE_LIMIT_SETTING = "MCP_AUTH_FAILURE_RATE_LIMIT"

REDIS_SCHEMES = frozenset({"redis", "rediss"})
REDIS_TIMEOUTS = {"socket_connect_timeout": 1, "socket_timeout": 1}

T = TypeVar("T")

__all__ = ["McpRateLimiter", "RateLimitConfigError", "StorageError", "parse_limit"]


class RateLimitConfigError(ValueError):
    """A limit setting or the storage is unusable; the message is safe to print."""


def parse_limit(setting: str, value: object) -> RateLimitItem:
    """Parse one rate such as ``120/minute``.

    Raises:
        RateLimitConfigError: For anything but exactly one rate of at least one
            request (several rates separated by ``;`` are refused, not truncated).
    """
    try:
        items = parse_many(value) if isinstance(value, str) else []
    except ValueError:
        items = []
    if len(items) != 1 or items[0].amount < 1:
        raise RateLimitConfigError(
            f"{setting} must be one rate such as '120/minute', not {value!r}."
        )
    return items[0]


def _redacted(uri: str) -> tuple[str, tuple[str, ...] | None]:
    """``uri`` without credentials, query or fragment, plus the parts taken out.

    An unparsable URI keeps only its scheme, and None says nothing of it is safe.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return f"{uri.partition(':')[0]}:...", None
    userinfo, _, host = parts.netloc.rpartition("@")
    safe = urlunsplit((parts.scheme, host, parts.path, "", ""))
    # A driver may echo the password alone or percent-decoded, so look for every form.
    taken = (userinfo, parts.password or "", parts.query, parts.fragment)
    forms = {form for part in taken if part for form in (part, unquote(part))}
    return safe, tuple(sorted(forms))


def build_storage(uri: str, options: dict | None) -> Storage:
    """The ``limits`` storage for ``uri``, with Redis socket timeouts defaulted to 1 s.

    Raises:
        RateLimitConfigError: For an unknown scheme, a malformed URI or a
            missing driver; the message never carries the URI's credentials.
    """
    options = dict(options or {})
    if uri.partition("://")[0].lower() in REDIS_SCHEMES:
        options = REDIS_TIMEOUTS | options
    try:
        return storage_from_string(uri, wrap_exceptions=True, **options)
    except (ConfigurationError, ValueError, TypeError) as error:
        safe, secrets = _redacted(uri)
        detail = str(error).replace(uri, safe)
        if secrets is None or any(secret in detail for secret in secrets):
            detail = type(error).__name__
        raise RateLimitConfigError(f"RATELIMIT_STORAGE_URI ({safe}) is unusable: {detail}") from None


class McpRateLimiter:
    """Both MCP limits on one storage; every check fails open on storage errors."""

    def __init__(
        self,
        strategy: RateLimiter,
        token_limit: RateLimitItem,
        failure_limit: RateLimitItem,
        key_prefix: str = "",
    ) -> None:
        self.strategy = strategy
        self.token_limit = token_limit
        self.failure_limit = failure_limit
        self._namespace = f"{key_prefix}:mcp" if key_prefix else "mcp"
        self._storage_down = False
        self._lock = threading.Lock()

    @classmethod
    def from_app(cls, app: Flask) -> McpRateLimiter:
        """Build the limiter from ``app.config``; invalid limits raise here, at start-up."""
        token_limit = parse_limit(TOKEN_LIMIT_SETTING, app.config.get(TOKEN_LIMIT_SETTING))
        failure_limit = parse_limit(FAILURE_LIMIT_SETTING, app.config.get(FAILURE_LIMIT_SETTING))
        storage = build_storage(
            app.config.get("RATELIMIT_STORAGE_URI") or "memory://",
            app.config.get("RATELIMIT_STORAGE_OPTIONS"),
        )
        strategy: RateLimiter = (
            MovingWindowRateLimiter(storage)
            if isinstance(storage, MovingWindowSupport)
            else FixedWindowRateLimiter(storage)
        )
        return cls(strategy, token_limit, failure_limit, app.config.get("RATELIMIT_KEY_PREFIX", ""))

    def hit_auth_failure(self, client_ip: str | None) -> int | None:
        """Count one failed bearer authentication; seconds to wait when over the limit, else None.

        One atomic ``hit``, so parallel failures cannot overshoot the limit.
        Requests without a client address share the ``-`` bucket.
        """
        key = self._key("auth-fail", client_ip or "-")
        return self._guarded(
            lambda: None
            if self.strategy.hit(self.failure_limit, key)
            else self._retry_after(self.failure_limit, key)
        )

    def hit_token(self, prefix: str) -> int | None:
        """Count one request for the token; seconds to wait when it is over its limit, else None."""
        key = self._key("token", prefix)
        return self._guarded(
            lambda: None
            if self.strategy.hit(self.token_limit, key)
            else self._retry_after(self.token_limit, key)
        )

    def _key(self, kind: str, identifier: str) -> str:
        return f"{self._namespace}:{kind}:{identifier}"

    def _retry_after(self, item: RateLimitItem, key: str) -> int:
        reset_time = self.strategy.get_window_stats(item, key).reset_time
        return max(1, math.ceil(reset_time - time.time()))

    def _guarded(self, check: Callable[[], T]) -> T | None:
        """Run ``check`` against the storage; on a storage error allow (None)."""
        try:
            result = check()
        except StorageError as error:
            self._set_storage_down(True, type(error.storage_error).__name__)
            return None
        if self._storage_down:
            self._set_storage_down(False)
        return result

    def _set_storage_down(self, down: bool, error_type: str = "") -> None:
        with self._lock:
            changed, self._storage_down = self._storage_down != down, down
        if changed and down:
            logger.warning(
                "MCP rate-limit storage unavailable (%s); requests are not limited "
                "until it answers again.", error_type,
            )
        elif changed:
            logger.info("MCP rate-limit storage reachable again; limits apply.")
