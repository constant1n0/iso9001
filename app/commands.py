"""Local administration commands."""

from __future__ import annotations

import getpass
import re
from datetime import datetime, timezone

import click
from flask import Flask, current_app
from flask.cli import with_appcontext
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import RoleEnum, User
from .services import api_tokens
from .services.actor import Actor
from .services.errors import Conflict, DomainError, NotFound
from .utils import security_logger


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_admin_input(
    username: str,
    email: str,
    password: str,
    password_confirmation: str,
) -> tuple[str, str]:
    """Validate initial administrator input before any database write."""
    normalized_username = username.strip()
    normalized_email = email.strip()

    if not 4 <= len(normalized_username) <= 150:
        raise click.ClickException("Username must contain 4 to 150 characters.")
    if len(normalized_email) > 255 or not EMAIL_PATTERN.fullmatch(normalized_email):
        raise click.ClickException("Enter a valid email address.")
    if len(password) < 8:
        raise click.ClickException("Password must contain at least 8 characters.")
    if password != password_confirmation:
        raise click.ClickException("Password confirmation does not match.")

    return normalized_username, normalized_email


def _user_exists() -> bool:
    """Return whether any account exists without loading account data."""
    return db.session.query(User.id).first() is not None


@click.command("create-admin")
@with_appcontext
def create_admin() -> None:
    """Create the initial administrator through the local CLI."""
    try:
        if _user_exists():
            raise click.ClickException(
                "An account already exists; the administrator was not created."
            )
    except SQLAlchemyError as error:
        db.session.rollback()
        raise click.ClickException(
            "Existing accounts could not be checked."
        ) from error

    username = click.prompt("Username", type=str)
    email = click.prompt("Email", type=str)
    password = click.prompt("Password", type=str, hide_input=True)
    password_confirmation = click.prompt(
        "Confirm password",
        type=str,
        hide_input=True,
    )
    username, email = _validate_admin_input(
        username,
        email,
        password,
        password_confirmation,
    )

    try:
        # This second check narrows the local operator race, but does not claim
        # cross-process serialization. Operators must run this command once.
        if _user_exists():
            raise click.ClickException(
                "An account already exists; the administrator was not created."
            )

        user = User(
            username=username,
            email=email,
            password=generate_password_hash(password, method="pbkdf2:sha256"),
            role=RoleEnum.ADMINISTRADOR,
        )
        db.session.add(user)
        db.session.commit()
    except click.ClickException:
        db.session.rollback()
        raise
    except SQLAlchemyError as error:
        db.session.rollback()
        raise click.ClickException(
            "The administrator could not be created."
        ) from error

    click.echo("Initial administrator created.")


def _cli_actor() -> Actor:
    """The operator running the command: administrator rights, ``cli`` channel.

    Server access is the credential, so there is no user row; the OS account is
    kept in the label for attribution.
    """
    try:
        label = f"cli:{getpass.getuser()}"
    except (KeyError, OSError):
        label = "cli"
    return Actor(user_id=None, label=label, role=RoleEnum.ADMINISTRADOR, channel="cli")


def _find_user(username: str) -> User:
    user = db.session.query(User).filter_by(username=username.strip()).one_or_none()
    if user is None:
        raise click.ClickException(f"User {username!r} not found.")
    return user


def _day(value: datetime | None) -> str:
    return "-" if value is None else value.strftime("%Y-%m-%d")


@click.command("create-api-token")
@click.option("--user", "username", required=True, help="Owner's username.")
@click.option("--name", required=True, help="Label, for example the client's name.")
@click.option(
    "--scope", "scopes", multiple=True, type=click.Choice(sorted(api_tokens.VALID_SCOPES)),
    help="read or write; repeat for both. Default: read.",
)
@click.option(
    "--days", type=int, default=api_tokens.DEFAULT_DAYS, show_default=True,
    help=f"Days until expiry (1 to {api_tokens.MAX_DAYS}).",
)
@with_appcontext
def create_api_token(username: str, name: str, scopes: tuple[str, ...], days: int) -> None:
    """Issue an API token for a user and print it once."""
    secret_key = current_app.config.get("SECRET_KEY")
    if not secret_key:
        raise click.ClickException(
            "SECRET_KEY is not configured; API tokens cannot be issued without it."
        )
    user = _find_user(username)
    actor = _cli_actor()
    try:
        plaintext, token = api_tokens.issue(
            db.session, actor, secret_key=secret_key,
            user_id=user.id, name=name, scopes=scopes or api_tokens.DEFAULT_SCOPES,
            days=days,
        )
        db.session.commit()
    except DomainError as error:
        db.session.rollback()
        raise click.ClickException(error.message) from error
    except SQLAlchemyError as error:
        db.session.rollback()
        raise click.ClickException("The API token could not be created.") from error

    security_logger.log_api_token_issued(token.prefix, user.username, actor.label)
    click.echo(
        f"API token {token.prefix} created for {user.username} "
        f"(scopes: {token.scopes}; expires {_day(token.expires_at)})."
    )
    click.echo("Copy it now: it is shown only once and cannot be recovered.")
    click.echo(plaintext)


@click.command("list-api-tokens")
@click.option("--user", "username", default=None, help="Only this user's tokens.")
@with_appcontext
def list_api_tokens(username: str | None) -> None:
    """List API tokens (never their secrets)."""
    user_id = _find_user(username).id if username is not None else None
    try:
        tokens = api_tokens.list_(db.session, _cli_actor(), user_id=user_id)
        owners = {u.id: u.username for u in db.session.query(User).all()}
    except (DomainError, SQLAlchemyError) as error:
        db.session.rollback()
        raise click.ClickException("The API tokens could not be listed.") from error
    if not tokens:
        click.echo("No API tokens.")
        return
    now = datetime.now(timezone.utc)
    click.echo(f"{'ID':>4}  {'PREFIX':<8}  {'USER':<15}  {'NAME':<20}  {'SCOPES':<10}  "
               f"{'STATUS':<8}  {'EXPIRES':<10}  LAST USED")
    for t in tokens:
        click.echo(
            f"{t.id:>4}  {t.prefix:<8}  {owners.get(t.user_id, '?'):<15.15}  {t.name:<20.20}  "
            f"{t.scopes:<10}  {api_tokens.status(t, now):<8}  {_day(t.expires_at):<10}  "
            f"{_day(t.last_used_at)}"
        )


@click.command("revoke-api-token")
@click.argument("prefix")
@with_appcontext
def revoke_api_token(prefix: str) -> None:
    """Revoke an API token by its 8-character prefix."""
    actor = _cli_actor()
    try:
        token = api_tokens.revoke(db.session, actor, prefix.strip().lower())
        db.session.commit()
    except NotFound as error:
        db.session.rollback()
        raise click.ClickException("API token not found.") from error
    except Conflict as error:
        db.session.rollback()
        raise click.ClickException("The API token was already revoked.") from error
    except (DomainError, SQLAlchemyError) as error:
        db.session.rollback()
        raise click.ClickException("The API token could not be revoked.") from error
    security_logger.log_api_token_revoked(token.prefix, actor.label)
    click.echo(f"API token {token.prefix} revoked.")


def register_commands(app: Flask) -> None:
    """Register local administration commands on the application."""
    app.cli.add_command(create_admin)
    app.cli.add_command(create_api_token)
    app.cli.add_command(list_api_tokens)
    app.cli.add_command(revoke_api_token)
