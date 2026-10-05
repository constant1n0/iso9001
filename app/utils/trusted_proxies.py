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

"""Trust ``X-Forwarded-*`` headers only when they come from a known reverse proxy.

Gunicorn passes the TCP peer through as ``REMOTE_ADDR``. Behind Traefik that
peer is always the proxy, so the client address must be read from
``X-Forwarded-For``; but any client can send that header, so it is honoured
only when the peer belongs to ``TRUSTED_PROXIES``.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable, Sequence
from typing import Any

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address
WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]]

# The values the middleware replaced, kept for debugging.
ORIGINAL_ENVIRON_KEY = "iso9001.trusted_proxies.orig"
_SCHEMES = ("http", "https")


class TrustedProxiesError(ValueError):
    """``TRUSTED_PROXIES`` cannot be used; the message is safe to print."""


def parse_trusted_proxies(value: str | Iterable[str] | None) -> tuple[Network, ...]:
    """Parse ``TRUSTED_PROXIES``: comma-separated IPv4/IPv6 addresses or networks.

    Args:
        value: The setting, as a comma-separated string or a list of entries.

    Returns:
        The trusted networks (a single address becomes a /32 or /128 network);
        empty when the setting is empty.

    Raises:
        TrustedProxiesError: For ``*``, a network that covers every address,
            or an entry that is not an address or a network (a network with
            host bits set, such as ``172.18.0.1/16``, is refused as a typo).
    """
    entries = value.split(",") if isinstance(value, str) else (value or ())
    networks = []
    for entry in (e.strip() for e in entries):
        if not entry:
            continue
        if entry == "*":
            raise TrustedProxiesError("TRUSTED_PROXIES must list addresses, not '*'.")
        try:
            network = ipaddress.ip_network(entry)
        except ValueError:
            raise TrustedProxiesError(
                f"TRUSTED_PROXIES has an invalid entry: {entry!r} "
                "(expected an IPv4/IPv6 address or a CIDR network)."
            ) from None
        if network.prefixlen == 0:
            raise TrustedProxiesError(
                f"TRUSTED_PROXIES must list addresses, not {entry!r} (like '*')."
            )
        networks.append(network)
    return tuple(networks)


def _address(text: str | None) -> Address | None:
    """Parse one address, unwrapping IPv4-mapped IPv6; ``None`` when malformed."""
    try:
        address = ipaddress.ip_address((text or "").strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


class TrustedProxyMiddleware:
    """WSGI middleware that resolves the client behind trusted proxies.

    When the direct peer is trusted, ``X-Forwarded-For`` is walked from right
    to left past trusted hops; the first untrusted hop becomes ``REMOTE_ADDR``
    (the left-most hop when all are trusted). A malformed hop ends the walk,
    since nothing left of it was vouched for by a trusted proxy.
    ``X-Forwarded-Proto`` (``http``/``https``) becomes ``wsgi.url_scheme``.
    Requests from any other peer pass through untouched.
    """

    def __init__(self, app: WSGIApp, trusted: Sequence[Network]) -> None:
        """Wrap ``app``; ``trusted`` must not be empty."""
        if not trusted:
            raise ValueError("TrustedProxyMiddleware needs at least one network.")
        self.app = app
        self.trusted = tuple(trusted)

    def _is_trusted(self, address: Address) -> bool:
        return any(address in network for network in self.trusted)

    def _client(self, forwarded_for: str) -> Address | None:
        """The right-most untrusted hop, else the left-most valid trusted one."""
        client = None
        for hop in reversed(forwarded_for.split(",")):
            address = _address(hop)
            if address is None:
                break
            client = address
            if not self._is_trusted(address):
                break
        return client

    def __call__(
        self, environ: dict[str, Any], start_response: Callable[..., Any]
    ) -> Iterable[bytes]:
        """Rewrite ``REMOTE_ADDR`` and the scheme for trusted peers, then delegate."""
        peer = _address(environ.get("REMOTE_ADDR"))
        if peer is not None and self._is_trusted(peer):
            environ[ORIGINAL_ENVIRON_KEY] = {
                "REMOTE_ADDR": environ.get("REMOTE_ADDR"),
                "wsgi.url_scheme": environ.get("wsgi.url_scheme"),
            }
            client = self._client(environ.get("HTTP_X_FORWARDED_FOR", ""))
            if client is not None:
                environ["REMOTE_ADDR"] = str(client)
            # The right-most value is the one set by the closest (trusted) proxy.
            proto = environ.get("HTTP_X_FORWARDED_PROTO", "").split(",")[-1]
            proto = proto.strip().lower()
            if proto in _SCHEMES:
                environ["wsgi.url_scheme"] = proto
        return self.app(environ, start_response)
