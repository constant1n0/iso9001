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

"""Scheduled audit e-mail notifications.

These functions run inside a Flask application context. Celery tasks in
``celery_worker.py`` are thin wrappers around them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app
from flask_mail import Message

from .extensions import mail
from .models import Auditoria, EstadoAuditoriaEnum, RoleEnum, User

logger = logging.getLogger(__name__)

UPCOMING_WINDOW_DAYS = 7
ACTIVE_AUDIT_STATES = (EstadoAuditoriaEnum.PENDIENTE, EstadoAuditoriaEnum.EN_PROCESO)


class NotificationDeliveryError(RuntimeError):
    """Raised after a run in which at least one recipient was not reached."""


def local_today() -> date:
    """Return today's date in the application's configured timezone."""
    return datetime.now(ZoneInfo(current_app.config["APP_TIMEZONE"])).date()


def send_pending_audits_report() -> int:
    """E-mail the list of pending audits to every administrator."""
    audits = (
        Auditoria.query.filter_by(estado=EstadoAuditoriaEnum.PENDIENTE)
        .order_by(Auditoria.fecha)
        .all()
    )
    if not audits:
        logger.info("No pending audits to report")
        return 0

    report = "\n".join(
        f"Auditoría ID: {a.id} - Área: {a.area_auditada} - "
        f"Fecha: {a.fecha:%Y-%m-%d}"
        for a in audits
    )
    return _send_to_role(
        RoleEnum.ADMINISTRADOR,
        subject="Reporte de Auditorías Pendientes",
        body=lambda user: (
            f"Hola {user.username},\n\n"
            f"Aquí está el reporte de auditorías pendientes:\n\n{report}"
        ),
    )


def send_upcoming_audits_alert(today: date | None = None) -> int:
    """E-mail auditors the active audits scheduled in the coming week."""
    start = today or local_today()
    end = start + timedelta(days=UPCOMING_WINDOW_DAYS)
    audits = (
        Auditoria.query.filter(
            Auditoria.fecha.between(start, end),
            Auditoria.estado.in_(ACTIVE_AUDIT_STATES),
        )
        .order_by(Auditoria.fecha)
        .all()
    )
    if not audits:
        logger.info("No upcoming audits between %s and %s", start, end)
        return 0

    summary = "\n".join(
        f"Auditoría en {a.area_auditada} - Fecha: {a.fecha:%Y-%m-%d}"
        for a in audits
    )
    return _send_to_role(
        RoleEnum.AUDITOR,
        subject="Recordatorio de Auditorías Próximas",
        body=lambda user: (
            f"Estimado/a {user.username},\n\n"
            "Estas son las auditorías programadas para los próximos "
            f"{UPCOMING_WINDOW_DAYS} días:\n\n{summary}"
        ),
    )


def _send_to_role(role: RoleEnum, subject: str, body) -> int:
    """Send one message per user with ``role``; keep going on failures."""
    recipients: Iterable[User] = User.query.filter_by(role=role).all()
    sender = current_app.config.get("MAIL_DEFAULT_SENDER")
    sent = 0
    failed: list[str] = []

    for user in recipients:
        if not user.email:
            logger.warning("User %s has no e-mail address; skipped", user.username)
            continue
        message = Message(
            subject=subject,
            sender=sender,
            recipients=[user.email],
            body=body(user),
        )
        try:
            mail.send(message)
        except Exception:
            logger.exception("Could not send %r to %s", subject, user.email)
            failed.append(user.email)
            continue
        sent += 1

    logger.info("%r sent to %d recipient(s)", subject, sent)
    if failed:
        raise NotificationDeliveryError(
            f"{subject!r} could not be delivered to {len(failed)} recipient(s)"
        )
    return sent
