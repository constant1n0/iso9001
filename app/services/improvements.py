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

"""Improvement register and web-only lifecycle (ISO 9001 clauses 10.1/10.3).

All roles may create and edit under the existing open-register policy. Only
transitions write lifecycle columns. Dates come from the adapter; writes lock
the record, validate before mutation, stamp and audit, flush, and never commit.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from functools import partial
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    MEJORA_LINKS, AccionRevision, EficaciaMejora, EstadoMejora, HallazgoAuditoria,
    Mejora, NoConformidad, ObjetivoCalidad, Oportunidad, OrigenMejora,
)
from . import audit, crud, fields, people, policy
from .actor import Actor
from .attribution import stamp_updated
from .errors import Conflict, NotFound, PermissionDenied, ValidationError
from .policy import Action, Resource

RESOURCE = Resource.IMPROVEMENTS
IDENTIFIED_IN_FUTURE = 'La fecha de identificación no puede ser futura.'
REVIEWER_IS_OWNER = 'La persona que revisa la eficacia no puede ser la responsable de la mejora.'
_LINK_TARGETS = {
    'no_conformidad_id': (NoConformidad, 'ninguna no conformidad'),
    'hallazgo_id': (HallazgoAuditoria, 'ningún hallazgo de auditoría'),
    'accion_revision_id': (AccionRevision, 'ninguna acción de revisión por la dirección'),
    'oportunidad_id': (Oportunidad, 'ninguna oportunidad'),
    'objetivo_id': (ObjetivoCalidad, 'ningún objetivo de calidad'),
}
SPEC = crud.Spec(
    model=Mejora,
    resource=RESOURCE,
    not_found='Mejora no encontrada.',
    fields=(
        crud.Field('descripcion', partial(fields.text, required=True, strip=False), required=True),
        crud.Field('responsable_id', fields.integer, check=people.check_reference),
        crud.Field('origen', partial(fields.enum_member, enum_cls=OrigenMejora)),
        *(crud.Field(column, fields.integer) for column in MEJORA_LINKS),
        crud.Field('fecha_identificacion', fields.required_date, required=True),
        crud.Field('beneficio', partial(fields.text, strip=False)),
    ),
    order_by=(Mejora.id_mejora,),
)
get = partial(crud.get, SPEC)
delete = partial(crud.delete, SPEC)


def _today(value: Any) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError('today must be a date')
    return value


def _locked(session: Session, record_id: Any) -> Mejora:
    found = session.scalars(select(Mejora).where(Mejora.id_mejora == record_id)
                            .with_for_update().execution_options(populate_existing=True)
                            ).one_or_none() if people.is_db_id(record_id) else None
    if found is None:
        raise NotFound(SPEC.not_found)
    return found


def _check_link(session: Session, column: str, value: int) -> None:
    """A newly chosen link must name an existing record of its declared kind."""
    model, nothing = _LINK_TARGETS[column]
    found = session.get(model, value) if people.is_db_id(value) else None
    if found is None:
        raise ValidationError(f'El campo «{column}» no corresponde a {nothing}.')


def _check(session: Session, data: Mapping[str, Any], found: Mejora | None, today: date) -> None:
    if not isinstance(data, Mapping):
        raise ValidationError('Los datos deben ser un objeto con campos.')
    fields.reject_unknown(data, SPEC.writable)
    values = {f.name: f.clean(data, f.name) for f in SPEC.fields if f.name in data}

    def final(key: str) -> Any:
        return values.get(key, getattr(found, key, None))

    if 'fecha_identificacion' in values and values['fecha_identificacion'] > today:
        raise ValidationError(IDENTIFIED_IN_FUTURE)
    origin = final('origen') or OrigenMejora.otro
    for column, (needed, _) in MEJORA_LINKS.items():
        if final(column) is not None and origin is not needed:
            if column in values:
                raise ValidationError(
                    f'El campo «{column}» solo se admite con el origen «{needed.value}».')
            raise ValidationError(
                f'La mejora sigue vinculada a su origen «{needed.value}» por «{column}»; '
                'quita antes ese vínculo para cambiar el origen.')
        if values.get(column) is not None and values[column] != getattr(found, column, None):
            _check_link(session, column, values[column])
    if found is not None and found.revisor_eficacia_id is not None:
        if final('responsable_id') == found.revisor_eficacia_id:
            raise ValidationError(REVIEWER_IS_OWNER)


def create(session: Session, actor: Actor, data: Mapping[str, Any], *, today: date) -> Mejora:
    """Propose an improvement; only description and identification date are required."""
    policy.require(actor, Action.CREATE, RESOURCE)
    _check(session, data, None, _today(today))
    return crud.create(SPEC, session, actor, data)


def update(session: Session, actor: Actor, record_id: int, data: Mapping[str, Any], *,
           today: date) -> Mejora:
    """Edit ordinary fields; a no-op leaves attribution and audit history unchanged."""
    policy.require(actor, Action.UPDATE, RESOURCE)
    # Unknown fields are rejected before loading a record, as in qms_changes.
    if isinstance(data, Mapping):
        fields.reject_unknown(data, SPEC.writable)
    found = _locked(session, record_id)
    _check(session, data, found, _today(today))
    return crud.update(SPEC, session, actor, record_id, data)


def _conditions(estado: EstadoMejora | str | None = None,
                origen: OrigenMejora | str | None = None,
                responsable_id: int | None = None) -> list[Any]:
    where = []
    for key, value, members in (('estado', estado, EstadoMejora), ('origen', origen, OrigenMejora)):
        if value is not None:
            where.append(getattr(Mejora, key) == fields.enum_member({key: value}, key, members))
    if responsable_id is not None:
        where.append(Mejora.responsable_id == responsable_id
                     if people.is_db_id(responsable_id) else false())
    return where


def list_(session: Session, actor: Actor, *, estado: EstadoMejora | str | None = None,
          origen: OrigenMejora | str | None = None,
          responsable_id: int | None = None) -> list[Mejora]:
    policy.require(actor, Action.READ, RESOURCE)
    return crud.list_(SPEC, session, actor, _conditions(estado, origen, responsable_id))


def list_page(session: Session, actor: Actor, *, estado: EstadoMejora | str | None = None,
              origen: OrigenMejora | str | None = None, responsable_id: int | None = None,
              page: int = 1, per_page: int = crud.DEFAULT_PER_PAGE) -> tuple[list[Mejora], int]:
    policy.require(actor, Action.READ, RESOURCE)
    return crud.list_page(SPEC, session, actor, _conditions(estado, origen, responsable_id),
                          page=page, per_page=per_page)


def _may(actor: Actor) -> bool:
    return policy.can(actor, Action.UPDATE, RESOURCE) and actor.channel == 'web'


def may_approve(actor: Actor, mejora: Mejora | None = None) -> bool:
    return _may(actor) and (mejora is None or mejora.estado is EstadoMejora.propuesta)


def may_implement(actor: Actor, mejora: Mejora | None = None) -> bool:
    return _may(actor) and (mejora is None or mejora.estado is EstadoMejora.aprobada)


def may_review_effectiveness(actor: Actor, mejora: Mejora | None = None) -> bool:
    return _may(actor) and (mejora is None or mejora.estado is EstadoMejora.implantada)


def may_cancel(actor: Actor, mejora: Mejora | None = None) -> bool:
    return _may(actor) and (mejora is None or mejora.estado in (
        EstadoMejora.propuesta, EstadoMejora.aprobada))


def _begin(session: Session, actor: Actor, record_id: int, today: date) -> Mejora:
    if not _may(actor):
        raise PermissionDenied()
    _today(today)
    return _locked(session, record_id)


def _move(session: Session, actor: Actor, found: Mejora, **values: Any) -> Mejora:
    before = audit.snapshot(found)
    for key, value in values.items():
        setattr(found, key, value)
    stamp_updated(found, actor)
    try:
        audit.record(session, actor, 'update', found, before=before)
        session.flush()
    except IntegrityError as exc:
        raise Conflict() from exc
    return found


def approve(session: Session, actor: Actor, record_id: int, *, today: date) -> Mejora:
    """Approve a proposal, recording the acting user's person and today's date."""
    found = _begin(session, actor, record_id, today)
    if found.estado is not EstadoMejora.propuesta:
        raise ValidationError('Solo se aprueba una mejora propuesta.')
    approver = people.of_user(session, actor.user_id)
    if approver is None:
        raise ValidationError('Tu usuario no está vinculado a ninguna persona; vincúlalo para aprobar la mejora.')
    if today < found.fecha_identificacion:
        raise ValidationError('La aprobación no puede ser anterior a la identificación.')
    return _move(session, actor, found, estado=EstadoMejora.aprobada,
                 aprobado_por_id=approver, fecha_aprobacion=today)


def implement(session: Session, actor: Actor, record_id: int, *, fecha: date,
              today: date) -> Mejora:
    """Record actual implementation, between approval and today, inclusive."""
    found = _begin(session, actor, record_id, today)
    if found.estado is not EstadoMejora.aprobada:
        raise ValidationError('Solo se implanta una mejora aprobada.')
    fecha = fields.required_date({'fecha_implementacion': fecha}, 'fecha_implementacion')
    if fecha > today:
        raise ValidationError('La fecha de implantación no puede ser futura.')
    if fecha < found.fecha_aprobacion:
        raise ValidationError('La fecha de implantación no puede ser anterior a la aprobación.')
    return _move(session, actor, found, estado=EstadoMejora.implantada, fecha_implementacion=fecha)


def review_effectiveness(session: Session, actor: Actor, record_id: int, *,
                         eficacia: EficaciaMejora | str, fecha: date, today: date,
                         comentario: str | None = None) -> Mejora:
    """Review an implementation independently; ineffective results need a comment."""
    found = _begin(session, actor, record_id, today)
    if found.estado is not EstadoMejora.implantada:
        raise ValidationError('Solo se revisa la eficacia de una mejora implantada.')
    result = fields.enum_member({'eficacia': eficacia}, 'eficacia', EficaciaMejora)
    fecha = fields.required_date({'fecha_eficacia': fecha}, 'fecha_eficacia')
    comment = fields.text({'comentario_eficacia': comentario}, 'comentario_eficacia')
    if result is EficaciaMejora.no_eficaz and comment is None:
        raise ValidationError('Explica por qué la mejora no ha sido eficaz («comentario_eficacia»).')
    if fecha < found.fecha_implementacion or fecha > today:
        raise ValidationError('La revisión de eficacia debe estar entre la implantación y hoy.')
    reviewer = people.of_user(session, actor.user_id)
    if reviewer is None:
        raise ValidationError('Tu usuario no está vinculado a ninguna persona; vincúlalo para revisar la eficacia.')
    if reviewer == found.responsable_id:
        raise ValidationError(REVIEWER_IS_OWNER)
    return _move(session, actor, found, estado=EstadoMejora.eficacia_revisada, eficacia=result,
                 fecha_eficacia=fecha, revisor_eficacia_id=reviewer, comentario_eficacia=comment)


def cancel(session: Session, actor: Actor, record_id: int, *, motivo: str | None,
           today: date) -> Mejora:
    """Cancel a proposal or approval with a reason; preserve any approval evidence."""
    found = _begin(session, actor, record_id, today)
    if found.estado not in (EstadoMejora.propuesta, EstadoMejora.aprobada):
        raise ValidationError('Solo se cancela una mejora propuesta o aprobada.')
    reason = fields.text({'motivo_cancelacion': motivo}, 'motivo_cancelacion')
    if reason is None:
        raise ValidationError('Indica el motivo de la cancelación («motivo_cancelacion»).')
    return _move(session, actor, found, estado=EstadoMejora.cancelada,
                 motivo_cancelacion=reason, fecha_cancelacion=today)
