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

"""User accounts: administration by administrators and self-service.

Administration (``list_``, ``get``, ``create``, ``update``, ``set_active``)
follows the ``USERS`` grant: administrators and auditors read, administrators
write, nobody deletes. Users are never deleted; ``set_active(False)`` takes
access away and also revokes the user's active API tokens, which needs the
API-token grant too (so the ``mcp`` channel cannot deactivate anyone).
Usernames cannot change after creation, to keep audit labels stable.

Self-service (``change_own_email``, ``change_own_password``) acts only on
``actor.user_id``, needs an active account and the current password, and is
refused on the ``mcp`` channel: a bearer token never changes credentials.

``User`` is outside ``AUDITED_MODELS``, so every write records its audit row
explicitly. Snapshots drop the ``password`` column; a password change is
audited as ``{"credential_changed": True}``. Security logging is the
adapter's job. Like every service this module flushes and never commits.

Errors:

- ``PermissionDenied``: the policy refuses the actor; self-service on the
  ``mcp`` channel, without a user id, or for a missing or inactive account.
- ``NotFound``: unknown user id.
- ``ValidationError``: unknown or missing keys, invalid values, changing your
  own role, deactivating yourself, a wrong current password, or a new
  password equal to the current one.
- ``Conflict``: a duplicate username or e-mail (e-mails compare
  case-insensitively), and demoting or deactivating the last active
  administrator.

The last-administrator check locks the active administrators' rows
(``SELECT ... FOR UPDATE``) so concurrent demotions on PostgreSQL serialize:
the second one waits and then sees the first one's change. SQLite ignores
the lock; production runs on PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from ..models import RoleEnum, User
from . import api_tokens, audit, fields, policy
from .actor import Actor
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource

HASH_METHOD = "pbkdf2:sha256"  # same as ``flask create-admin`` and the reset flow
USERNAME_MIN, USERNAME_MAX = 4, 150
CREATE_FIELDS = frozenset({"username", "email", "role", "password"})
UPDATE_FIELDS = frozenset({"email", "role"})

NOT_FOUND = "Usuario no encontrado."
USERNAME_LENGTH = (
    f"El nombre de usuario debe tener entre {USERNAME_MIN} y {USERNAME_MAX} caracteres."
)
ACTIVE_FLAG = "El estado de la cuenta debe ser verdadero o falso."
DUPLICATE_USERNAME = "Ya existe un usuario con ese nombre de usuario."
DUPLICATE_EMAIL = "Ya existe un usuario con ese correo electrónico."
DUPLICATE_ACCOUNT = "Ya existe un usuario con ese nombre de usuario o correo electrónico."
OWN_ROLE = "No puedes cambiar tu propio rol."
OWN_DEACTIVATION = "No puedes desactivar tu propia cuenta."
LAST_ADMINISTRATOR = (
    "No se puede desactivar ni retirar el rol al último administrador activo."
)
WRONG_PASSWORD = "La contraseña actual no es correcta."
SAME_PASSWORD = "La nueva contraseña debe ser distinta de la actual."


def list_(session: Session, actor: Actor) -> list[User]:
    """Every user, active or not, ordered by username."""
    policy.require(actor, Action.READ, Resource.USERS)
    return list(session.scalars(select(User).order_by(User.username)))


def get(session: Session, actor: Actor, user_id: int) -> User:
    """One user or ``NotFound``."""
    policy.require(actor, Action.READ, Resource.USERS)
    return _load(session, user_id)


def create(session: Session, actor: Actor, data: Mapping[str, Any]) -> User:
    """Create an active user; every key in ``CREATE_FIELDS`` is required."""
    policy.require(actor, Action.CREATE, Resource.USERS)
    fields.reject_unknown(data, CREATE_FIELDS)
    fields.require_keys(data, CREATE_FIELDS)
    username = _username(data)
    email = fields.email(data, "email")
    role = fields.enum_member(data, "role", RoleEnum)
    password = fields.new_password(data, "password")
    if session.scalar(select(User.id).where(User.username == username)) is not None:
        raise Conflict(DUPLICATE_USERNAME)
    _ensure_email_free(session, email)
    user = User(
        username=username,
        email=email,
        role=role,
        password=generate_password_hash(password, method=HASH_METHOD),
        active=True,
    )
    session.add(user)
    try:
        audit.record(session, actor, "create", user)  # flushes to obtain the id
    except IntegrityError as exc:
        raise Conflict(DUPLICATE_ACCOUNT) from exc
    return user


def update(
    session: Session, actor: Actor, user_id: int, data: Mapping[str, Any]
) -> User:
    """Change ``email`` and/or ``role``; a call that changes nothing writes nothing."""
    policy.require(actor, Action.UPDATE, Resource.USERS)
    user = _load(session, user_id)
    fields.reject_unknown(data, UPDATE_FIELDS)
    values: dict[str, Any] = {}
    if "email" in data:
        values["email"] = fields.email(data, "email")
    if "role" in data:
        values["role"] = fields.enum_member(data, "role", RoleEnum)
    changes = {key: value for key, value in values.items() if getattr(user, key) != value}
    if not changes:
        return user
    if "role" in changes:
        if actor.user_id == user.id:
            raise ValidationError(OWN_ROLE)
        if _is_active_administrator(user):
            _ensure_another_active_administrator(session, user.id)
    if "email" in changes:
        _ensure_email_free(session, changes["email"], own_id=user.id)
    before = audit.snapshot(user)
    for key, value in changes.items():
        setattr(user, key, value)
    _record_update(session, actor, user, before)
    return user


def set_active(
    session: Session,
    actor: Actor,
    user_id: int,
    active: bool,
    *,
    now: datetime | None = None,
) -> User:
    """Deactivate or reactivate an account; a call that changes nothing writes nothing.

    Deactivation revokes every active API token of the user (each audited);
    reactivation does not restore them.
    """
    policy.require(actor, Action.UPDATE, Resource.USERS)
    if not isinstance(active, bool):
        raise ValidationError(ACTIVE_FLAG)
    if not active:
        policy.require(actor, Action.UPDATE, Resource.API_TOKENS)
    user = _load(session, user_id)
    if user.active == active:
        return user
    if not active:
        if actor.user_id == user.id:
            raise ValidationError(OWN_DEACTIVATION)
        if _is_active_administrator(user):
            _ensure_another_active_administrator(session, user.id)
    before = audit.snapshot(user)
    user.active = active
    _record_update(session, actor, user, before)
    if not active:
        api_tokens.revoke_all_for_user(session, actor, user.id, now=now)
    return user


def change_own_email(
    session: Session, actor: Actor, *, current_password: object, new_email: object
) -> User:
    """Change the actor's own e-mail after checking the current password."""
    user = _own_account(session, actor)
    _check_current_password(user, current_password)  # before any lookup: no probing
    email = fields.email({"email": new_email}, "email")
    if email == user.email:
        return user
    _ensure_email_free(session, email, own_id=user.id)
    before = audit.snapshot(user)
    user.email = email
    _record_update(session, actor, user, before)
    return user


def change_own_password(
    session: Session, actor: Actor, *, current_password: object, new_password: object
) -> User:
    """Change the actor's own password after checking the current one."""
    user = _own_account(session, actor)
    _check_current_password(user, current_password)
    password = fields.new_password({"password": new_password}, "password")
    if check_password_hash(user.password, password):
        raise ValidationError(SAME_PASSWORD)
    user.password = generate_password_hash(password, method=HASH_METHOD)
    _record_update(session, actor, user, {}, after={"credential_changed": True})
    return user


def _load(session: Session, user_id: int) -> User:
    found = session.get(User, user_id)
    if found is None:
        raise NotFound(NOT_FOUND)
    return found


def _username(data: Mapping[str, Any]) -> str:
    """Trimmed, 4 to 150 characters: the rule of ``flask create-admin``."""
    value = data["username"]
    if isinstance(value, str) and USERNAME_MIN <= len(value.strip()) <= USERNAME_MAX:
        return value.strip()
    raise ValidationError(USERNAME_LENGTH)


def _ensure_email_free(session: Session, email: str, own_id: int | None = None) -> None:
    """Raise ``Conflict`` when another user holds ``email``, ignoring case."""
    query = select(User.id).where(func.lower(User.email) == email)
    if own_id is not None:
        query = query.where(User.id != own_id)
    if session.scalar(query) is not None:
        raise Conflict(DUPLICATE_EMAIL)


def _is_active_administrator(user: User) -> bool:
    return user.role is RoleEnum.ADMINISTRADOR and user.is_active


def _ensure_another_active_administrator(session: Session, user_id: int) -> None:
    """Raise ``Conflict`` unless an active administrator other than ``user_id`` remains.

    ``FOR UPDATE`` cannot be combined with an aggregate on PostgreSQL, so the
    locked ids are counted here.
    """
    administrators = session.scalars(
        select(User.id)
        .where(User.role == RoleEnum.ADMINISTRADOR, User.active.is_(True))
        .with_for_update()
    ).all()
    if not any(admin_id != user_id for admin_id in administrators):
        raise Conflict(LAST_ADMINISTRATOR)


def _own_account(session: Session, actor: Actor) -> User:
    """The actor's own active account; tokens (``mcp``) never change credentials."""
    if actor.channel == "mcp" or actor.user_id is None:
        raise PermissionDenied()
    user = session.get(User, actor.user_id)
    if user is None or not user.is_active:
        raise PermissionDenied()
    return user


def _check_current_password(user: User, candidate: object) -> None:
    if not (
        isinstance(candidate, str)
        and candidate
        and check_password_hash(user.password, candidate)
    ):
        raise ValidationError(WRONG_PASSWORD)


def _record_update(
    session: Session,
    actor: Actor,
    user: User,
    before: dict[str, Any],
    after: dict[str, Any] | None = None,
) -> None:
    try:
        audit.record(session, actor, "update", user, before=before, after=after)
        session.flush()
    except IntegrityError as exc:
        raise Conflict(DUPLICATE_ACCOUNT) from exc
