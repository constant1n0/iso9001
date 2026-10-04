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

"""API tokens: bearer credentials that let an adapter act as a user.

A token is ``iso_<prefix>_<secret>``: an 8-hex random prefix used for lookup
and a 256-bit URL-safe secret. Only ``HMAC-SHA256(key, token)`` is stored; the
key is derived from the application's ``SECRET_KEY``, so rotating that key
invalidates every token. The plaintext is returned once, by :func:`issue`.

Like every service this module never imports Flask: the adapter passes
``secret_key`` explicitly, owns the commit and writes the security log (see
``app.utils.security_logger``) from the data in :class:`AuthenticationFailed`.
:func:`authenticate` flushes ``last_used_at`` but never commits.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ApiToken, User
from . import audit, policy
from .actor import Actor
from .errors import AuthenticationFailed, AuthFailure, Conflict, NotFound, ValidationError
from .policy import Action, Resource

VALID_SCOPES = frozenset({"read", "write"})
DEFAULT_SCOPES = ("read",)  # least privilege
DEFAULT_DAYS = 90
MAX_DAYS = 365
NAME_MAX_LENGTH = 100
LAST_USED_INTERVAL = timedelta(minutes=5)

ACTIVE, EXPIRED, REVOKED = "active", "expired", "revoked"

_KEY_DOMAIN = b"iso9001-api-token-v1"
_TOKEN = re.compile(r"iso_([0-9a-f]{8})_([A-Za-z0-9_-]{43})")
_PREFIX = re.compile(r"[0-9a-f]{8}")
_PREFIX_ATTEMPTS = 5
_DUMMY_HASH = "0" * 64


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    """Timezone-aware UTC; some backends return aware columns as naive UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _derive_key(secret_key: str | bytes | None) -> bytes:
    if not secret_key:
        raise ValueError("A non-empty secret key is required")
    raw = secret_key.encode("utf-8") if isinstance(secret_key, str) else bytes(secret_key)
    return hmac.new(raw, _KEY_DOMAIN, hashlib.sha256).digest()


def _digest(secret_key: str | bytes | None, raw: str) -> str:
    return hmac.new(_derive_key(secret_key), raw.encode("ascii"), hashlib.sha256).hexdigest()


def _require_secret_key(secret_key: str | bytes | None) -> None:
    """Fail early on an unusable key, before anything is written or compared."""
    _derive_key(secret_key)


def status(token: ApiToken, now: datetime | None = None) -> str:
    """``revoked``, ``expired`` or ``active``: the one rule for a token's validity."""
    if token.revoked_at is not None:
        return REVOKED
    moment = now if now is not None else _now()
    return EXPIRED if _utc(token.expires_at) <= moment else ACTIVE


def _scopes(value: Iterable[str] | None) -> str:
    if value is None or isinstance(value, str):
        raise ValidationError("Los permisos deben indicarse como una lista.")
    scopes = set(value)
    if not scopes or not scopes <= VALID_SCOPES:
        raise ValidationError("Los permisos admitidos son «read» y «write».")
    return " ".join(sorted(scopes))


def _days(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_DAYS:
        raise ValidationError(f"La caducidad debe estar entre 1 y {MAX_DAYS} días.")
    return value


def _name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("El nombre del token es obligatorio.")
    value = value.strip()
    if len(value) > NAME_MAX_LENGTH:
        raise ValidationError(
            f"El nombre admite como máximo {NAME_MAX_LENGTH} caracteres."
        )
    return value


def _new_prefix(session: Session) -> str:
    for _ in range(_PREFIX_ATTEMPTS):
        prefix = secrets.token_hex(4)
        if session.scalar(select(ApiToken.id).where(ApiToken.prefix == prefix)) is None:
            return prefix
    raise Conflict("No se ha podido generar un identificador de token único.")


def issue(
    session: Session,
    actor: Actor,
    *,
    secret_key: str | bytes,
    user_id: int,
    name: str,
    scopes: Iterable[str] = DEFAULT_SCOPES,
    days: int = DEFAULT_DAYS,
    now: datetime | None = None,
) -> tuple[str, ApiToken]:
    """Create a token for ``user_id``; return ``(plaintext, row)``.

    The plaintext is not recoverable afterwards. The audit row records the
    token's metadata only: ``token_hash`` is dropped by the audit recorder.
    """
    policy.require(actor, Action.CREATE, Resource.API_TOKENS)
    _require_secret_key(secret_key)
    name, scope_text, days = _name(name), _scopes(scopes), _days(days)
    if session.get(User, user_id) is None:
        raise NotFound("Usuario no encontrado.")
    moment = now if now is not None else _now()
    prefix = _new_prefix(session)
    plaintext = f"iso_{prefix}_{secrets.token_urlsafe(32)}"
    token = ApiToken(
        user_id=user_id,
        name=name,
        prefix=prefix,
        token_hash=_digest(secret_key, plaintext),
        scopes=scope_text,
        created_at=moment,
        created_by_label=actor.label,
        expires_at=moment + timedelta(days=days),
    )
    session.add(token)
    try:
        audit.record(session, actor, "create", token)
    except IntegrityError as error:
        raise Conflict("No se ha podido crear el token.") from error
    return plaintext, token


def authenticate(
    session: Session,
    raw: object,
    *,
    secret_key: str | bytes,
    now: datetime | None = None,
) -> Actor:
    """Return the ``mcp`` actor for a valid bearer token or raise.

    Malformed, unknown, tampered, expired and revoked tokens, and tokens whose
    owner no longer exists or is inactive, all raise
    :class:`AuthenticationFailed` with the same message. The actor carries the
    owner's *current* role and the token's scopes. ``last_used_at`` is flushed
    at most every five minutes.
    """
    _require_secret_key(secret_key)
    match = _TOKEN.fullmatch(raw) if isinstance(raw, str) else None
    if match is None:
        raise AuthenticationFailed(AuthFailure.MALFORMED)
    prefix = match.group(1)
    row = session.scalar(select(ApiToken).where(ApiToken.prefix == prefix))
    candidate = _digest(secret_key, raw)
    # Compare against a dummy when the prefix is unknown so both paths cost the same.
    stored = row.token_hash if row is not None else _DUMMY_HASH
    matches = hmac.compare_digest(candidate, stored)
    if row is None:
        raise AuthenticationFailed(AuthFailure.UNKNOWN_PREFIX, prefix)
    if not matches:
        raise AuthenticationFailed(AuthFailure.BAD_SECRET, prefix)
    moment = now if now is not None else _now()
    state = status(row, moment)
    if state == REVOKED:
        raise AuthenticationFailed(AuthFailure.REVOKED, prefix)
    if state == EXPIRED:
        raise AuthenticationFailed(AuthFailure.EXPIRED, prefix)
    user = session.get(User, row.user_id)
    if user is None:
        raise AuthenticationFailed(AuthFailure.USER_MISSING, prefix)
    if not user.is_active:
        raise AuthenticationFailed(AuthFailure.USER_INACTIVE, prefix)
    if row.last_used_at is None or moment - _utc(row.last_used_at) >= LAST_USED_INTERVAL:
        row.last_used_at = moment
        session.flush()
    return Actor.from_user(
        user, channel="mcp", scopes=frozenset(row.scopes.split()) & VALID_SCOPES
    )


def revoke(
    session: Session,
    actor: Actor,
    token_ref: int | str,
    *,
    now: datetime | None = None,
) -> ApiToken:
    """Revoke a token by id or by its 8-hex prefix; the caller commits."""
    policy.require(actor, Action.UPDATE, Resource.API_TOKENS)
    if isinstance(token_ref, int) and not isinstance(token_ref, bool):
        token = session.get(ApiToken, token_ref)
    elif isinstance(token_ref, str) and _PREFIX.fullmatch(token_ref):
        token = session.scalar(select(ApiToken).where(ApiToken.prefix == token_ref))
    else:
        token = None
    if token is None:
        raise NotFound("Token no encontrado.")
    if token.revoked_at is not None:
        raise Conflict("El token ya estaba revocado.")
    before = audit.snapshot(token)
    token.revoked_at = now if now is not None else _now()
    token.revoked_by_label = actor.label
    session.flush()
    audit.record(session, actor, "update", token, before=before)
    return token


def list_(session: Session, actor: Actor, user_id: int | None = None) -> list[ApiToken]:
    """Every token (revoked and expired included), oldest first."""
    policy.require(actor, Action.READ, Resource.API_TOKENS)
    query = select(ApiToken).order_by(ApiToken.id)
    if user_id is not None:
        query = query.where(ApiToken.user_id == user_id)
    return list(session.scalars(query))
