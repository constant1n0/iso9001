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

"""Quality objective screens (ISO 9001 clause 6.2): adapters over ``quality_objectives``.

Task QO-2 of ``quality-objectives``. Every role reads the list and an
objective's page; administrators and auditors create and edit objectives,
change their state and add and edit measurements; administrators delete
(policy ``QUALITY_OBJECTIVES``, decision QO4).

- The list filters by year, state, owner and process and shows each
  objective's target, latest value, whether it meets the target and whether a
  measurement is overdue (``summary`` against ``local_today``).
- An objective's page lists its measurements and embeds the trend chart's data
  as JSON (``objective-data``), drawn by ``static/js/objectives.js``.
- A state change (``/<id>/estado/<state>``) is an ordinary update; a segment
  that names no state (by member name) is a 404 that writes nothing. The
  measurement forms live under their objective; one asked for under another
  objective is a 404, and while the objective is not ``activo`` they return to
  its page with the reason, so its measurements stay read-only.
- Once measured, the target and its direction are shown locked on the edit form
  and left out of the update (the service refuses changing them).
- The yearly PDF (``/pdf?anio=``, task QO-3) has the list's access. Without
  ``anio``, or with it blank, it covers the current year; a year that is not
  four digits from 2000 to 2100 is a 404. It counts the year's objectives by
  state, the active ones meeting their target and those with an overdue
  measurement, then lists each objective by deadline and title with its
  measurements. A PDF that cannot be rendered is logged and flashed on the list.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed; a refused delete or state change
is flashed on the objective's page.
"""

import re
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Any

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, url_for,
)
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (
    CHOOSE_PERSON, MedicionObjetivoForm, ObjetivoCalidadForm, number_text, person_choices,
    process_choices,
)
from ..models import EstadoObjetivo, MedicionObjetivo, ObjetivoCalidad, ProcesoOperacion
from ..services import crud, people, process_operations
from ..services import quality_objectives as qo
from ..services.actor import Actor
from ..services.errors import NotFound
from ..utils.pdf import pdf_response, render_pdf
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('objetivo_calidad', __name__, url_prefix='/objetivos-calidad')

OBJECTIVE_FIELDS = ('titulo', 'descripcion', 'proceso_id', 'responsable_id', 'indicador',
                    'unidad', 'meta', 'sentido', 'valor_inicial', 'frecuencia', 'fecha_inicio',
                    'fecha_limite', 'anio', 'recursos', 'evaluacion')
TARGET_FIELDS = ('target', 'sentido')  # form attributes (``target`` posts ``meta``)
MEASUREMENT_FIELDS = ('fecha', 'valor', 'registrado_por_id', 'comentario')
STATES = frozenset(state.name for state in EstadoObjetivo)
# The state buttons of an objective's page: (state, label, confirmation).
CLOSED_NOTE = ' Sus mediciones quedarán cerradas.'
CLOSE_STATES = (
    ('alcanzado', 'Alcanzado', '¿Dar el objetivo por alcanzado?' + CLOSED_NOTE),
    ('no_alcanzado', 'No alcanzado', '¿Dar el objetivo por no alcanzado?' + CLOSED_NOTE),
    ('cancelado', 'Cancelar', '¿Cancelar el objetivo?' + CLOSED_NOTE),
)
REOPEN = (('activo', 'Reactivar', None),)
YEAR_TEXT = re.compile(r'[0-9]{4}')


@bp.app_template_filter('cifra')
def number_or_dash(value: Decimal | None) -> str:
    """A stored number as ``number_text`` writes it, or a dash when there is none."""
    return '—' if value is None else number_text(value)


def _objective_page(objective_id: int) -> ResponseReturnValue:
    return redirect(url_for('objetivo_calidad.show_objective', objective_id=objective_id))


def _processes(actor: Actor) -> list[ProcesoOperacion]:
    """Every process, by name, for the picker, the filter and the labels."""
    found = crud.list_(process_operations.SPEC, db.session, actor)
    return sorted(found, key=lambda process: process.proceso.casefold())


def _measurements(actor: Actor, objective_id: int) -> list[MedicionObjetivo]:
    return qo.measurements.list_(db.session, actor, objetivo_id=objective_id)


def _is_measured(actor: Actor, objective_id: int) -> bool:
    return qo.measurements.list_page(db.session, actor, objetivo_id=objective_id,
                                     per_page=1)[1] > 0


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'quality_objectives')
def list_objectives() -> ResponseReturnValue:
    """Objectives matching the filters with their status; ``applied`` counts the filters used.

    An ``estado`` that names no state, or a year, owner or process that is not
    a number, is ignored, so it neither filters nor counts. The measurements of
    every listed objective come in one query, however many are listed.
    """
    actor = current_actor()
    filters = {name: request.args.get(name, type=int)
               for name in ('anio', 'responsable_id', 'proceso_id')}
    estado = request.args.get('estado', '')
    filters['estado'] = estado if estado in STATES else None
    applied = sum(value is not None for value in filters.values())
    every = qo.objectives.list_(db.session, actor)
    found = qo.objectives.list_(db.session, actor, **filters) if applied else every
    today = local_today()
    owners = people.names(db.session, actor, (o.responsable_id for o in every))
    processes = _processes(actor)
    measured = qo.measurements.by_objective(db.session, actor, [o.id for o in found])
    return render_template(
        'objetivos_calidad/list.html',
        objetivos=found,
        anio_pdf=filters['anio'] if _valid_year(filters['anio']) else today.year,
        resumenes={o.id: qo.summary(o, measured[o.id], today=today) for o in found},
        personas=owners,
        responsables=sorted(owners.items(), key=lambda item: item[1].casefold()),
        anios=sorted({o.anio for o in every}, reverse=True),
        estados=list(EstadoObjetivo),
        procesos=processes,
        filtros=filters,
        applied=applied,
    )


@bp.route('/<int:objective_id>', methods=['GET'])
@login_required
@require_permission('read', 'quality_objectives')
def show_objective(objective_id: int) -> ResponseReturnValue:
    """An objective's page: its fields, status, measurements and trend chart data."""
    actor = current_actor()
    objetivo = qo.objectives.get(db.session, actor, objective_id)
    mediciones = _measurements(actor, objective_id)
    cumple = {m.id: qo.meets(m.valor, objetivo.meta, objetivo.sentido) for m in mediciones}
    activo = objetivo.estado is EstadoObjetivo.activo
    return render_template(
        'objetivos_calidad/detail.html',
        objetivo=objetivo,
        mediciones=mediciones,
        cumple=cumple,
        resumen=qo.summary(objetivo, mediciones, today=local_today()),
        activo=activo,
        transiciones=CLOSE_STATES if activo else REOPEN,
        proceso=_process_name(actor, objetivo.proceso_id),
        personas=people.names(db.session, actor, (
            objetivo.responsable_id, *(m.registrado_por_id for m in mediciones))),
        grafica={
            'fechas': [m.fecha.isoformat() for m in mediciones],
            'valores': [float(m.valor) for m in mediciones],
            'cumple': [cumple[m.id] for m in mediciones],
            'meta': float(objetivo.meta),
            'sentido': objetivo.sentido.value,
            'unidad': objetivo.unidad or '',
            'indicador': objetivo.indicador,
        },
    )


def _valid_year(year: int | None) -> bool:
    return year is not None and qo.YEAR_MIN <= year <= qo.YEAR_MAX


def _report_year(text: str | None, today: date) -> int:
    """The year ``anio`` names, or ``today``'s when it is missing or blank; else a 404."""
    if not text:
        return today.year
    if YEAR_TEXT.fullmatch(text) and _valid_year(int(text)):
        return int(text)
    abort(404)


def _year_context(actor: Actor, year: int, today: date) -> dict[str, Any]:
    """The year's objectives by deadline and title, with their measurements and status.

    ``por_estado`` counts them by state, in the enum's order; ``en_meta`` and
    ``vencidas`` count the active ones meeting their target and those with an
    overdue measurement on ``today``.
    """
    found = sorted(qo.objectives.list_(db.session, actor, anio=year),
                   key=lambda o: (o.fecha_limite, o.titulo.casefold(), o.id))
    measured = qo.measurements.by_objective(db.session, actor, [o.id for o in found])
    resumenes = {o.id: qo.summary(o, measured[o.id], today=today) for o in found}
    active = [resumenes[o.id] for o in found if o.estado is EstadoObjetivo.activo]
    states = Counter(o.estado for o in found)
    return dict(
        anio=year,
        objetivos=found,
        mediciones=measured,
        resumenes=resumenes,
        cumple={m.id: qo.meets(m.valor, o.meta, o.sentido)
                for o in found for m in measured[o.id]},
        por_estado={state: states[state] for state in EstadoObjetivo},
        en_meta=sum(resumen['cumple'] is True for resumen in active),
        vencidas=sum(resumen['medicion_vencida'] for resumen in active),
        procesos={p.id_proceso: p.proceso for p in _processes(actor)},
        personas=people.names(db.session, actor, (o.responsable_id for o in found)),
    )


@bp.route('/pdf', methods=['GET'])
@login_required
@require_permission('read', 'quality_objectives')
def export_pdf() -> ResponseReturnValue:
    """The year's objectives as a PDF (``?anio=``; the current year by default)."""
    actor = current_actor()
    today = local_today()
    year = _report_year(request.args.get('anio'), today)
    context = _year_context(actor, year, today)
    try:
        pdf = render_pdf('objetivos_calidad/pdf_template.html', **context)
    except Exception:
        current_app.logger.exception('Error generating PDF for quality objectives %s', year)
        flash('Error al generar el PDF de los objetivos de calidad.', 'danger')
        return redirect(url_for('objetivo_calidad.list_objectives'))
    return pdf_response(pdf, f'objetivos_calidad_{year}.pdf')


def _process_name(actor: Actor, process_id: int | None) -> str | None:
    if process_id is None:
        return None
    try:
        return process_operations.get(db.session, actor, process_id).proceso
    except NotFound:
        return None


# -- objectives ------------------------------------------------------------------------


def _objective_form(actor: Actor, objetivo: ObjetivoCalidad | None = None, *,
                    locked: bool = False) -> ObjetivoCalidadForm:
    """The objective form with its pickers; a ``locked`` target has no fields."""
    form = ObjetivoCalidadForm(obj=objetivo)
    if objetivo is not None and request.method == 'GET':
        form.target.data = objetivo.meta
    form.proceso_id.choices = process_choices(_processes(actor))
    form.responsable_id.choices = person_choices(
        people.choices(db.session, actor, include=getattr(objetivo, 'responsable_id', None)),
        empty=CHOOSE_PERSON)
    if locked:
        for name in TARGET_FIELDS:
            delattr(form, name)
    return form


def _objective_data(form: ObjetivoCalidadForm) -> dict[str, Any]:
    """The service payload by posted name; a blank year is left out (the service keeps
    or derives it)."""
    data = {f.name: f.data for f in form if f.name in OBJECTIVE_FIELDS}
    if data.get('anio') is None:
        data.pop('anio', None)
    return data


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'quality_objectives')
def new_objective() -> ResponseReturnValue:
    actor = current_actor()
    form = _objective_form(actor)
    if form.validate_on_submit() and (created := saved(
        lambda: qo.objectives.create(db.session, actor, _objective_data(form))
    )):
        flash(f'Objetivo de calidad «{created.titulo}» creado.', 'success')
        return _objective_page(created.id)
    return render_template('objetivos_calidad/form.html', form=form, objetivo=None)


@bp.route('/<int:objective_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'quality_objectives')
def edit_objective(objective_id: int) -> ResponseReturnValue:
    """Edit an objective; once measured, its target and direction are shown locked."""
    actor = current_actor()
    objetivo = qo.objectives.get(db.session, actor, objective_id)
    locked = _is_measured(actor, objective_id)
    form = _objective_form(actor, objetivo, locked=locked)
    if form.validate_on_submit() and saved(
        lambda: qo.objectives.update(db.session, actor, objective_id, _objective_data(form))
    ):
        flash('Objetivo de calidad actualizado.', 'success')
        return _objective_page(objective_id)
    return render_template('objetivos_calidad/form.html', form=form, objetivo=objetivo,
                           bloqueada=locked)


@bp.route('/<int:objective_id>/estado/<estado>', methods=['POST'])
@login_required
@require_permission('update', 'quality_objectives')
def change_state(objective_id: int, estado: str) -> ResponseReturnValue:
    """Close an active objective (reached, not reached, cancelled) or reopen a closed one."""
    if estado not in STATES:  # a member name only; anything else is no route
        abort(404)
    if committed(lambda: qo.objectives.update(db.session, current_actor(), objective_id,
                                              {'estado': estado})):
        flash(f'Estado del objetivo: {EstadoObjetivo[estado].value}.', 'success')
    return _objective_page(objective_id)


@bp.route('/<int:objective_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'quality_objectives')
def delete_objective(objective_id: int) -> ResponseReturnValue:
    """Delete an objective without measurements (administrators)."""
    if committed(lambda: qo.objectives.delete(db.session, current_actor(), objective_id)):
        flash('Objetivo de calidad eliminado.', 'success')
        return redirect(url_for('objetivo_calidad.list_objectives'))
    return _objective_page(objective_id)


# -- measurements ----------------------------------------------------------------------


def _active(actor: Actor, objective_id: int) -> ObjetivoCalidad | None:
    """The objective while it takes measurements; otherwise flash why and return ``None``."""
    objetivo = qo.objectives.get(db.session, actor, objective_id)
    if objetivo.estado is not EstadoObjetivo.activo:
        flash(qo.OBJECTIVE_NOT_ACTIVE, 'danger')
        return None
    return objetivo


def _measurement(actor: Actor, objective_id: int, measurement_id: int) -> MedicionObjetivo:
    """A measurement of the objective; one of another objective is a 404."""
    found = qo.measurements.get(db.session, actor, measurement_id)
    if found.objetivo_id != objective_id:
        raise NotFound(qo.MEASUREMENT_NOT_FOUND)
    return found


def _measurement_form(actor: Actor,
                      medicion: MedicionObjetivo | None = None) -> MedicionObjetivoForm:
    """The measurement form; the recorder picker keeps the stored one even when inactive."""
    form = MedicionObjetivoForm(obj=medicion)
    form.registrado_por_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(medicion, 'registrado_por_id', None)))
    return form


@bp.route('/<int:objective_id>/mediciones/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'quality_objectives')
def new_measurement(objective_id: int) -> ResponseReturnValue:
    """Record a measurement; the form starts on today and on the user's own person."""
    actor = current_actor()
    objetivo = _active(actor, objective_id)
    if objetivo is None:
        return _objective_page(objective_id)
    form = _measurement_form(actor)
    if request.method == 'GET':
        form.fecha.data = local_today()
        form.registrado_por_id.data = people.of_user(db.session, actor.user_id)
    if form.validate_on_submit() and saved(lambda: qo.measurements.create(
        db.session, actor, form_data(form, MEASUREMENT_FIELDS) | {'objetivo_id': objective_id},
        today=local_today(),
    )):
        flash('Medición registrada.', 'success')
        return _objective_page(objective_id)
    return render_template('objetivos_calidad/measurement_form.html', form=form,
                           objetivo=objetivo, medicion=None)


@bp.route('/<int:objective_id>/mediciones/<int:measurement_id>/editar',
          methods=['GET', 'POST'])
@login_required
@require_permission('update', 'quality_objectives')
def edit_measurement(objective_id: int, measurement_id: int) -> ResponseReturnValue:
    actor = current_actor()
    medicion = _measurement(actor, objective_id, measurement_id)
    objetivo = _active(actor, objective_id)
    if objetivo is None:
        return _objective_page(objective_id)
    form = _measurement_form(actor, medicion)
    if form.validate_on_submit() and saved(lambda: qo.measurements.update(
        db.session, actor, measurement_id, form_data(form, MEASUREMENT_FIELDS),
        today=local_today(),
    )):
        flash('Medición actualizada.', 'success')
        return _objective_page(objective_id)
    return render_template('objetivos_calidad/measurement_form.html', form=form,
                           objetivo=objetivo, medicion=medicion)


@bp.route('/<int:objective_id>/mediciones/<int:measurement_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'quality_objectives')
def delete_measurement(objective_id: int, measurement_id: int) -> ResponseReturnValue:
    """Delete a measurement of an active objective (administrators)."""
    actor = current_actor()
    _measurement(actor, objective_id, measurement_id)
    if _active(actor, objective_id) is not None and committed(
            lambda: qo.measurements.delete(db.session, actor, measurement_id)):
        flash('Medición eliminada.', 'success')
    return _objective_page(objective_id)
