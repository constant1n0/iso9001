"""Local administration commands."""

from __future__ import annotations

import re

import click
from flask import Flask
from flask.cli import with_appcontext
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import RoleEnum, User


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


def register_commands(app: Flask) -> None:
    """Register local administration commands on the application."""
    app.cli.add_command(create_admin)
