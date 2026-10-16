# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""Server-side limits of a signed-in web session (decision DD4).

The session cookie is a browser-session cookie (no ``Expires``), so it ends
when the browser closes. On top of that, sign-in stamps the signed session with
the sign-in time and the last-seen time, in UTC epoch seconds, and ``enforce``
signs out a session idle for more than ``SESSION_IDLE_TIMEOUT`` or signed in
more than ``SESSION_ABSOLUTE_LIFETIME`` ago.

Bearer-token clients never reach this hook: the MCP server is a separate ASGI
application that authenticates each request by its token and has no session.
"""

from __future__ import annotations

import logging
import time
from collections.abc import MutableMapping
from typing import Any

from flask import Flask, current_app, flash, redirect, request, session
from flask_login import current_user, login_url, logout_user
from werkzeug.wrappers import Response

from .utils.security_logger import get_client_ip

SIGNED_IN_AT = "_signed_in_at"
SEEN_AT = "_seen_at"
# The last-seen time is rewritten at most this often, so the cookie is not
# re-signed and re-sent on every request.
SEEN_REFRESH_SECONDS = 60
EXPIRED_MESSAGE = "La sesión ha caducado. Inicie sesión de nuevo."

security_logger = logging.getLogger("security")

# The module's clock (UTC epoch seconds); tests shift it.
_clock = time.time


def _now() -> int:
    return int(_clock())


def start(web_session: MutableMapping[str, Any]) -> None:
    """Stamp a session that has just signed in with its sign-in and last-seen time."""
    now = _now()
    web_session[SIGNED_IN_AT] = now
    web_session[SEEN_AT] = now


def end(web_session: MutableMapping[str, Any]) -> None:
    """Remove the stamps from a session that signs out."""
    web_session.pop(SIGNED_IN_AT, None)
    web_session.pop(SEEN_AT, None)


def _stamp(name: str) -> int | None:
    value = session.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _expiry_reason(now: int) -> str | None:
    """Why the session has expired: ``idle``, ``absolute``, ``unstamped``; else None."""
    signed_in_at, seen_at = _stamp(SIGNED_IN_AT), _stamp(SEEN_AT)
    # A session without stamps was signed in before they were introduced (or
    # opened by a remember cookie, which is never issued): it is treated as
    # expired, so its user signs in again.
    if signed_in_at is None or seen_at is None:
        return "unstamped"
    config = current_app.config
    if now - seen_at > config["SESSION_IDLE_TIMEOUT"].total_seconds():
        return "idle"
    if now - signed_in_at > config["SESSION_ABSOLUTE_LIFETIME"].total_seconds():
        return "absolute"
    return None


def enforce() -> Response | None:
    """``before_request``: sign out an expired session, else refresh its last-seen time.

    An expired session gets the same answer as any unauthenticated request
    (``login_manager.unauthorized``: a redirect to the sign-in page, JSON
    requests included), only with the expiry message.
    """
    if request.endpoint == "static" or not current_user.is_authenticated:
        return None
    now = _now()
    reason = _expiry_reason(now)
    if reason is None:
        if now - session[SEEN_AT] > SEEN_REFRESH_SECONDS:
            session[SEEN_AT] = now
        return None
    security_logger.info(
        f"SESSION_EXPIRED | user={current_user.username} | ip={get_client_ip()}"
        f" | reason={reason}"
    )
    # Clear first: logout_user() then marks a remember cookie, if one was sent,
    # for deletion, and that mark must survive or the cookie would sign the
    # user in (and out) again on every request.
    session.clear()
    logout_user()
    flash(EXPIRED_MESSAGE, "warning")
    login_view = current_app.login_manager.login_view
    return redirect(login_url(login_view, next_url=request.url))


def init_app(app: Flask) -> None:
    """Register the session limits on every request of ``app``."""
    app.before_request(enforce)
