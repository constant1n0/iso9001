"""Rate limits for the MCP HTTP transport.

Two limits, counted with the ``limits`` library on the web application's
rate-limit storage (``RATELIMIT_STORAGE_URI``: Redis in production, memory
otherwise, which is consistent because the MCP server is one process):

- requests per API token, after authentication (``MCP_TOKEN_RATE_LIMIT``);
- failed bearer authentications per client address
  (``MCP_AUTH_FAILURE_RATE_LIMIT``), checked before the database lookup.

A storage failure never blocks a request: the limiter fails open, logging one
warning when the storage stops answering and one info line when it is back
(the error type only, never the storage URI). The stdio transport has no
limits: it runs locally as one configured token, so whoever can start it
already has access to the server itself.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from flask import Flask
from limits import RateLimitItem, parse_many
from limits.errors import StorageError
from limits.storage import MovingWindowSupport, storage_from_string
from limits.strategies import FixedWindowRateLimiter, MovingWindowRateLimiter, RateLimiter

logger = logging.getLogger(__name__)

TOKEN_LIMIT_SETTING = "MCP_TOKEN_RATE_LIMIT"
FAILURE_LIMIT_SETTING = "MCP_AUTH_FAILURE_RATE_LIMIT"

T = TypeVar("T")

__all__ = ["McpRateLimiter", "RateLimitConfigError", "StorageError", "parse_limit"]


class RateLimitConfigError(ValueError):
    """A limit setting is not a single valid rate; the message is safe to print."""


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
        storage = storage_from_string(
            app.config.get("RATELIMIT_STORAGE_URI") or "memory://",
            wrap_exceptions=True,
            **(app.config.get("RATELIMIT_STORAGE_OPTIONS") or {}),
        )
        strategy: RateLimiter = (
            MovingWindowRateLimiter(storage)
            if isinstance(storage, MovingWindowSupport)
            else FixedWindowRateLimiter(storage)
        )
        return cls(strategy, token_limit, failure_limit, app.config.get("RATELIMIT_KEY_PREFIX", ""))

    def auth_failures_exceeded(self, client_ip: str | None) -> int | None:
        """Seconds ``client_ip`` must wait when it is over the failure limit, else None.

        Only reads the counter: a refused request is not a failed authentication.
        """
        key = self._key("auth-fail", client_ip or "-")
        return self._guarded(
            lambda: None
            if self.strategy.test(self.failure_limit, key)
            else self._retry_after(self.failure_limit, key)
        )

    def hit_auth_failure(self, client_ip: str | None) -> None:
        """Count one failed bearer authentication from ``client_ip``."""
        key = self._key("auth-fail", client_ip or "-")
        self._guarded(lambda: self.strategy.hit(self.failure_limit, key))

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
