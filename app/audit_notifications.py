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

"""Scheduled e-mail notifications: audits, the monthly report and document reviews.

These functions run inside a Flask application context. Celery tasks in
``celery_worker.py`` are thin wrappers around them. Recipients are active
users with an e-mail address; anyone else is skipped and the skip is logged
(by user name or person id, never by address).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app
from flask_mail import Message

from .extensions import db, mail
from .models import Auditoria, Document, EstadoAuditoriaEnum, Person, RoleEnum, User
from .services import document_revisions, documents, people
from .services.actor import Actor
from .utils import reports

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


def send_monthly_quality_report(today: date | None = None) -> int:
    """E-mail administrators the quality summary of the month that just ended.

    The job runs on the 1st, so the report covers the previous calendar month
    and is dated its last day.
    """
    day = (today or local_today()).replace(day=1) - timedelta(days=1)
    if not _recipients(RoleEnum.ADMINISTRADOR):
        logger.info("No administrators with e-mail for the monthly report")
        return 0

    pdf = reports.generar_reporte_pdf(day)
    return _send_to_role(
        RoleEnum.ADMINISTRADOR,
        subject="Reporte Mensual del Sistema de Gestión de Calidad",
        body=lambda user: (
            f"Hola {user.username},\n\n"
            "Adjunto encontrarás el reporte mensual del Sistema de Gestión "
            "de Calidad."
        ),
        attachment=(f"reporte-calidad-{day:%Y-%m}.pdf", "application/pdf", pdf),
    )


# The document review alert reads as the system, with an administrator's reach.
_REVIEW_READER = Actor(None, "document-review-alert", RoleEnum.ADMINISTRADOR, "system")


def send_document_review_alert(today: date | None = None) -> int:
    """E-mail the periodic reviews that are due (DC5 of ``document-control``).

    A review is due when a document still in use has its next review date on
    or before ``today``. Each owner hears about their own documents through
    the user linked to the person, limited to what that user may read (an
    operativo only the documents in force); administrators get the whole list,
    with each owner, in a single message. Owners without a user account are
    skipped and logged.
    """
    day = today or local_today()
    due = documents.due_for_review(db.session, _REVIEW_READER, today=day)
    if not due:
        logger.info("No document reviews due on %s", day)
        return 0

    owners = people.names(db.session, _REVIEW_READER, (d.owner_id for d in due))
    admins = _recipients(RoleEnum.ADMINISTRADOR)
    letters = [(user, _review_body(user, due, day, owners)) for user in admins]
    in_force = document_revisions.effective(db.session, _REVIEW_READER, (d.id for d in due))
    for user, owned in _owner_users(due, skip={user.id for user in admins}):
        readable = [d for d in owned
                    if user.role in document_revisions.DRAFT_READERS or d.id in in_force]
        if readable:
            letters.append((user, _review_body(user, readable, day)))
    return _deliver("Revisiones periódicas de documentos pendientes", letters)


def _owner_users(
    due: list[Document], skip: set[int]
) -> list[tuple[User, list[Document]]]:
    """Each reachable user linked to an owner, with the due documents that person owns.

    Users in ``skip`` (the administrators, who get the whole list) are left out.
    """
    by_owner: dict[int, list[Document]] = {}
    for document in due:
        if document.owner_id is not None:
            by_owner.setdefault(document.owner_id, []).append(document)
    found = []
    for person_id, owned in by_owner.items():
        person = db.session.get(Person, person_id)
        user = None if person is None or person.user_id is None else db.session.get(
            User, person.user_id)
        if user is None:
            logger.info("Document owner person %s has no user account; skipped", person_id)
        elif user.id not in skip and _reachable(user):
            found.append((user, owned))
    return found


def _review_body(
    user: User, due: list[Document], day: date, owners: dict[int, str] | None = None
) -> str:
    """The alert text; with ``owners`` (the administrators' copy) each line names the owner."""
    lines = []
    for document in due:
        line = (f"- {document.code} · {document.title} · revisión prevista el "
                f"{document.next_review_date:%d/%m/%Y}")
        if owners is not None:
            line += f" · propietario: {owners.get(document.owner_id, 'sin propietario')}"
        lines.append(line)
    listing = "\n".join(lines)
    return (
        f"Hola {user.username},\n\n"
        "Estos documentos tienen pendiente su revisión periódica a fecha de "
        f"{day:%d/%m/%Y}:\n\n{listing}\n\n"
        "Revísalos en el sistema y actualiza su fecha de próxima revisión."
    )


def _reachable(user: User) -> bool:
    """Whether ``user`` can receive mail: active and with an address (skips are logged)."""
    if not user.is_active:
        logger.info("User %s is inactive; skipped", user.username)
        return False
    if not user.email:
        logger.warning("User %s has no e-mail address; skipped", user.username)
        return False
    return True


def _recipients(role: RoleEnum) -> list[User]:
    """Active users with ``role`` that have an e-mail address."""
    users: Iterable[User] = User.query.filter_by(role=role).all()
    return [user for user in users if _reachable(user)]


def _send_to_role(
    role: RoleEnum,
    subject: str,
    body: Callable[[User], str],
    attachment: tuple[str, str, bytes] | None = None,
) -> int:
    """Send one message per user with ``role``; keep going on failures."""
    return _deliver(subject, [(user, body(user)) for user in _recipients(role)], attachment)


def _deliver(
    subject: str,
    letters: Iterable[tuple[User, str]],
    attachment: tuple[str, str, bytes] | None = None,
) -> int:
    """Send each ``(user, body)``; keep going on failures, then raise if any failed."""
    sender = current_app.config.get("MAIL_DEFAULT_SENDER")
    sent = 0
    failed: list[str] = []

    for user, body in letters:
        message = Message(
            subject=subject,
            sender=sender,
            recipients=[user.email],
            body=body,
        )
        if attachment:
            message.attach(*attachment)
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
