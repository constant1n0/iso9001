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

"""Training records (``Capacitacion``) on the generic CRUD helper.

Validation mirrors ``CapacitacionForm``; every role may use the register.
``persona_id`` cites the person trained (``personas``, see
``people.check_reference``); ``personal`` keeps the legacy free-text name.
``personal`` is required unless a person is cited: a write that leaves it
blank takes the person's name (``people.fill_name``). Its field is therefore
not flagged ``required`` in ``SPEC`` (which the MCP registry mirrors); the
``prepare`` hook requires it on create once the person's name is filled in.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from functools import partial
from typing import Any

from sqlalchemy.orm import Session

from ..models import Capacitacion
from . import crud, fields, people
from .actor import Actor
from .policy import Resource

TEMA_MAX = 100  # mirrors Capacitacion.tema and the web form
PERSONAL_MAX = 100  # mirrors Capacitacion.personal and the web form
EVALUACION_MAX = 20  # mirrors Capacitacion.evaluacion_final and the web form
# Required on create; ``personal`` may come from the cited person instead.
REQUIRED_ON_CREATE = frozenset({"tema", "fecha", "personal"})


def _prepare(session: Session, data: Any, found: Capacitacion | None) -> Any:
    """Fill a blank ``personal`` from the cited person, then require it on create."""
    data = people.fill_name(session, data, found, link="persona_id", text="personal",
                            max_length=PERSONAL_MAX)
    if found is None and isinstance(data, Mapping):
        fields.require_keys(data, REQUIRED_ON_CREATE)
    return data


SPEC = crud.Spec(
    model=Capacitacion,
    resource=Resource.TRAINING,
    not_found="Capacitación no encontrada.",
    fields=(
        crud.Field("tema", partial(fields.text, required=True, max_length=TEMA_MAX), required=True),
        crud.Field("fecha", fields.required_date, required=True),
        # Not ``required``: a cited person can supply it (see ``_prepare``).
        crud.Field("personal", partial(fields.text, required=True, max_length=PERSONAL_MAX)),
        crud.Field("duracion_horas", partial(fields.integer, minimum=0)),
        crud.Field("evaluacion_final", partial(fields.text, max_length=EVALUACION_MAX)),
        crud.Field("persona_id", fields.integer, check=people.check_reference),
    ),
    order_by=(Capacitacion.fecha.desc(), Capacitacion.id.desc()),
    prepare=_prepare,
)

get = partial(crud.get, SPEC)
create = partial(crud.create, SPEC)
update = partial(crud.update, SPEC)
delete = partial(crud.delete, SPEC)


def _conditions(tema: str | None, fecha: date | None, personal: str | None) -> list[Any]:
    """The filters shared by ``list_`` and ``list_page``; they combine with AND."""
    where: list[Any] = []
    if tema:
        where.append(Capacitacion.tema.ilike(f"%{tema}%"))
    if fecha:
        where.append(Capacitacion.fecha == fecha)
    if personal:
        where.append(Capacitacion.personal.ilike(f"%{personal}%"))
    return where


def list_(
    session: Session,
    actor: Actor,
    *,
    tema: str | None = None,
    fecha: date | None = None,
    personal: str | None = None,
) -> list[Capacitacion]:
    """Training records, newest first; filters combine with AND."""
    return crud.list_(SPEC, session, actor, _conditions(tema, fecha, personal))


def list_page(
    session: Session,
    actor: Actor,
    *,
    tema: str | None = None,
    fecha: date | None = None,
    personal: str | None = None,
    page: int = 1,
    per_page: int = crud.DEFAULT_PER_PAGE,
) -> tuple[list[Capacitacion], int]:
    """One page in the ``list_`` order plus the total matching the same filters."""
    return crud.list_page(SPEC, session, actor, _conditions(tema, fecha, personal),
                          page=page, per_page=per_page)
