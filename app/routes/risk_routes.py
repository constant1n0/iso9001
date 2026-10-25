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

"""Risk and opportunity screens (ISO 9001 clause 6.1): adapters over ``risks``.

Task RO-3 of ``risks-opportunities``. Two registers with the same screens, one
blueprint each: risks under ``/riesgos`` (``riesgo``) and opportunities under
``/oportunidades`` (``oportunidad``). Every role reads them; administrators and
auditors create and edit records and add, edit and evaluate actions;
administrators delete (policy ``RISKS_OPPORTUNITIES``, decision RO6).

- The list filters by state, level (or priority), owner, process, origin,
  context issue (``cuestion_id``, which the issue's page links),
  ``sin_valorar``, ``revision_vencida`` and ``abierto`` (``1`` or ``0``); risks
  also by one cell (``probabilidad`` and ``impacto``) and by ``alto_sin_accion``,
  what the dashboard flags. A value that names nothing is ignored, so it
  neither filters nor counts. Each record shows its level or priority (or "Sin
  valorar"), its next review with an overdue badge and its open actions.
- Above the risks sits the 5 × 5 heat map: likelihood on rows (5 at the top),
  impact on columns, each cell counting the open assessed risks there
  (``risks.heat_map``) and linking to the list filtered by that cell.
- The form offers the context issues in force (OC1 of ``organization-context``),
  plus the one a record already names, marked when superseded.
- A record's page shows every field, its context issue linked, the level before
  and after the actions
  and its actions, each with an overdue badge, and the changes to the QMS it gives
  rise to (CP6 of ``change-planning``). Actions live under their record;
  one asked for under another record is a 404. A closed record takes no new
  actions, and only a completed action is evaluated for effectiveness
  (``/eficacia``); either form asked for otherwise returns to the record's page
  with the reason.
- A record with actions, or one a change names, offers no delete; the service
  refuses it anyway.
- Each list links its register as a PDF (``/pdf``, task RO-4), which every role
  reads: a header with the open records by level (or priority), the unassessed
  ones and those overdue for review, and for risks the heat map as a table; then
  the open records and, in their own section, the closed ones, each ordered by
  level (or priority) from the highest, unassessed last, then by title, with its
  actions. A PDF that cannot be rendered is logged and flashed on the list.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed; a refused delete is flashed on the
record's page.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (
    CHOOSE_PERSON, AccionRiesgoForm, EficaciaAccionForm, OportunidadForm, RiesgoForm,
    issue_choices, party_choices, person_choices, process_choices,
)
from ..models import (
    ESCALA_MAX, ESCALA_MIN, AccionRiesgo, EstadoAccionRiesgo, EstadoOportunidad, EstadoRiesgo,
    NivelRiesgo, OrigenRiesgo, PrioridadOportunidad, ProcesoOperacion, nivel,
)
from ..services import context_issues, crud, people, process_operations, stakeholders
from ..services import risks as ro
from ..services.actor import Actor
from ..services.errors import NotFound
from ..utils.pdf import pdf_response, render_pdf
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .change_routes import citing_changes
from .people_routes import form_data, saved

RESOURCE = 'risks_opportunities'
RECORD_FIELDS = ('titulo', 'descripcion', 'origen', 'parte_interesada_id', 'cuestion_id',
                 'proceso_id', 'responsable_id', 'estado', 'fecha_identificacion',
                 'fecha_revision', 'notas')
ACTION_FIELDS = ('descripcion', 'responsable_id', 'fecha_limite', 'estado', 'fecha_completada')
EVALUATION_FIELDS = ('eficacia', 'fecha_evaluacion', 'comentario_eficacia')
FLAGS = {'1': True, '0': False}
YES_NO = [('1', 'Sí'), ('0', 'No')]
SCALE = [(str(n), str(n)) for n in range(ESCALA_MIN, ESCALA_MAX + 1)]

# A list filter: query name, label, choices (value, text) and the service value of a choice.
Filter = tuple[str, str, list[tuple[str, str]], Callable[[str], Any]]


def _members(enum_cls: type) -> list[tuple[str, str]]:
    return [(member.name, member.value) for member in enum_cls]


@dataclass(frozen=True)
class Kind:
    """One register's screens: its blueprint, service, form and the words its pages use."""

    bp: Blueprint
    register: Any  # ``risks.risks`` or ``risks.opportunities``
    form: type
    fields: tuple[str, ...]  # posted to the service
    estados: type
    band: str  # the computed filter and attribute: ``nivel`` or ``prioridad``
    bands: type
    parent_key: str  # the actions' column: ``riesgo_id`` or ``oportunidad_id``
    new_path: str
    closed: str  # why a closed record takes no new actions
    labels: dict[str, str]
    extra_filters: tuple[Filter, ...] = ()


RISKS = Kind(
    bp=Blueprint('riesgo', __name__, url_prefix='/riesgos'),
    register=ro.risks, form=RiesgoForm,
    fields=(*RECORD_FIELDS, 'probabilidad', 'impacto', 'tratamiento', 'probabilidad_residual',
            'impacto_residual'),
    estados=EstadoRiesgo, band='nivel', bands=NivelRiesgo, parent_key='riesgo_id',
    new_path='nuevo', closed=ro.RISK_CLOSED,
    labels=dict(plural='Riesgos', one='Riesgo', new='Nuevo riesgo', edit='Editar el riesgo',
                band='Nivel', open='Abierto', created='Riesgo «{}» creado.',
                updated='Riesgo actualizado.', deleted='Riesgo eliminado.',
                delete='¿Eliminar el riesgo «{}»?', empty='Todavía no hay riesgos.',
                no_match='Ningún riesgo coincide con los filtros.',
                report='Registro de riesgos', file='riesgos',
                pdf_error='Error al generar el PDF de los riesgos.',
                open_many='Riesgos abiertos', closed_many='Riesgos cerrados',
                no_open='No hay riesgos abiertos.', no_closed='No hay riesgos cerrados.'),
    extra_filters=(('probabilidad', 'Probabilidad', SCALE, int),
                   ('impacto', 'Impacto', SCALE, int),
                   ('alto_sin_accion', 'Alto o crítico sin acción abierta', YES_NO, FLAGS.get)),
)
OPPORTUNITIES = Kind(
    bp=Blueprint('oportunidad', __name__, url_prefix='/oportunidades'),
    register=ro.opportunities, form=OportunidadForm,
    fields=(*RECORD_FIELDS, 'beneficio', 'viabilidad', 'decision'),
    estados=EstadoOportunidad, band='prioridad', bands=PrioridadOportunidad,
    parent_key='oportunidad_id', new_path='nueva', closed=ro.OPPORTUNITY_CLOSED,
    labels=dict(plural='Oportunidades', one='Oportunidad', new='Nueva oportunidad',
                edit='Editar la oportunidad', band='Prioridad', open='Abierta',
                created='Oportunidad «{}» creada.', updated='Oportunidad actualizada.',
                deleted='Oportunidad eliminada.', delete='¿Eliminar la oportunidad «{}»?',
                empty='Todavía no hay oportunidades.',
                no_match='Ninguna oportunidad coincide con los filtros.',
                report='Registro de oportunidades', file='oportunidades',
                pdf_error='Error al generar el PDF de las oportunidades.',
                open_many='Oportunidades abiertas',
                closed_many='Oportunidades aprovechadas o descartadas',
                no_open='No hay oportunidades abiertas.',
                no_closed='No hay oportunidades aprovechadas ni descartadas.'),
)
risks_bp, opportunities_bp = RISKS.bp, OPPORTUNITIES.bp


def _page(kind: Kind, record_id: int) -> ResponseReturnValue:
    return redirect(url_for(f'{kind.bp.name}.show_record', record_id=record_id))


def _processes(actor: Actor) -> list[ProcesoOperacion]:
    """Every process, by name, for the picker, the filter and the labels."""
    found = crud.list_(process_operations.SPEC, db.session, actor)
    return sorted(found, key=lambda process: process.proceso.casefold())


def _name(get: Callable[..., Any], actor: Actor, record_id: int | None, attribute: str) -> Any:
    """``attribute`` of the record ``get`` finds, or ``None`` when there is none."""
    if record_id is None:
        return None
    try:
        return getattr(get(db.session, actor, record_id), attribute)
    except NotFound:
        return None


# -- the list --------------------------------------------------------------------------


def _filters(kind: Kind, owners: dict[int, str], processes: list[ProcesoOperacion],
             issues: list[Any]) -> list[Filter]:
    """The list's filters, in the form's order; every context issue, superseded too."""
    return [
        ('estado', 'Estado', _members(kind.estados), str),
        (kind.band, kind.labels['band'], _members(kind.bands), str),
        ('responsable_id', 'Responsable',
         [(str(person_id), nombre)
          for person_id, nombre in sorted(owners.items(), key=lambda item: item[1].casefold())],
         int),
        ('proceso_id', 'Proceso', [(str(p.id_proceso), p.proceso) for p in processes], int),
        ('origen', 'Origen', _members(OrigenRiesgo), str),
        ('cuestion_id', 'Cuestión del contexto', [(str(i.id), i.titulo) for i in issues], int),
        ('sin_valorar', 'Sin valorar', YES_NO, FLAGS.get),
        ('revision_vencida', 'Revisión vencida', YES_NO, FLAGS.get),
        ('abierto', kind.labels['open'], YES_NO, FLAGS.get),
        *kind.extra_filters,
    ]


def _open_actions(actor: Actor, kind: Kind) -> Counter:
    """How many pending or in-progress actions each record of ``kind`` has (two queries)."""
    counts: Counter = Counter()
    for estado in ro.OPEN_ACTION_STATES:
        for action in ro.actions.list_(db.session, actor, estado=estado):
            if (parent := getattr(action, kind.parent_key)) is not None:
                counts[parent] += 1
    return counts


def _heat_map(actor: Actor, chosen: dict[str, Any]) -> list[tuple[int, list[dict[str, Any]]]]:
    """The heat map's rows, likelihood 5 first, each with its cells by impact.

    A cell links to the open risks in it; ``actual`` marks the cell the list shows.
    """
    counts = ro.heat_map(db.session, actor)
    shown = (chosen.get('probabilidad'), chosen.get('impacto'))
    rows = []
    for likelihood in range(ESCALA_MAX, ESCALA_MIN - 1, -1):
        cells = [{'impacto': impact,
                  'cuantos': counts.get((likelihood, impact), 0),
                  'nivel': nivel(likelihood, impact),
                  'url': url_for('riesgo.list_records', probabilidad=likelihood,
                                 impacto=impact, abierto=1),
                  'actual': shown == (likelihood, impact)}
                 for impact in range(ESCALA_MIN, ESCALA_MAX + 1)]
        rows.append((likelihood, cells))
    return rows


def _list(kind: Kind) -> ResponseReturnValue:
    """Records matching the filters; ``applied`` counts the filters used."""
    actor = current_actor()
    today = local_today()
    every = kind.register.list_(db.session, actor)
    owners = people.names(db.session, actor, (r.responsable_id for r in every))
    processes = _processes(actor)
    fields = _filters(kind, owners, processes, context_issues.issues.list_(db.session, actor))
    chosen, selected = {}, {}
    for name, _, choices, value_of in fields:
        text = request.args.get(name, '')
        if text in {value for value, _ in choices}:
            selected[name], chosen[name] = text, value_of(text)
    found = kind.register.list_(db.session, actor, today=today, **chosen) if chosen else every
    return render_template(
        'riesgos/list.html',
        kind=kind,
        registros=found,
        abiertas=_open_actions(actor, kind),
        vencidas={r.id for r in found if ro.revision_vencida(r, today=today)},
        personas=owners,
        procesos={p.id_proceso: p.proceso for p in processes},
        campos=fields,
        elegidos=selected,
        applied=len(chosen),
        mapa=_heat_map(actor, chosen) if kind is RISKS else None,
        bandas=ro.LEVEL_RANGES.items(),
    )


# -- the register as a PDF -------------------------------------------------------------


def _report(kind: Kind, actor: Actor, today: date) -> dict[str, Any]:
    """The register PDF's context on ``today``.

    Records are ordered by level (or priority) from the highest, the unassessed
    last, then by title; ``abiertos`` and ``cerrados`` split them. ``recuentos``
    counts the open ones by band, highest first; ``acciones`` holds each record's
    actions by deadline.
    """
    ranks = {band: rank for rank, band in enumerate(reversed(list(kind.bands)))}
    every = sorted(kind.register.list_(db.session, actor), key=lambda record: (
        ranks.get(getattr(record, kind.band), len(ranks)), record.titulo.casefold(), record.id))
    opened = [record for record in every if ro.is_open(record)]
    acciones: defaultdict[int, list[AccionRiesgo]] = defaultdict(list)
    for action in sorted(ro.actions.list_(db.session, actor),
                         key=lambda action: (action.fecha_limite, action.id)):
        if (parent := getattr(action, kind.parent_key)) is not None:
            acciones[parent].append(action)
    owned = [r.responsable_id for r in every]
    owned += [a.responsable_id for found in acciones.values() for a in found]
    bands = Counter(getattr(record, kind.band) for record in opened)
    return dict(
        kind=kind,
        hoy=today,
        abiertos=opened,
        cerrados=[record for record in every if not ro.is_open(record)],
        acciones=dict(acciones),
        recuentos=[(band.value, bands[band]) for band in reversed(list(kind.bands))],
        sin_valorar=bands[None],
        vencidas={r.id for r in opened if ro.revision_vencida(r, today=today)},
        acciones_vencidas={a.id for found in acciones.values() for a in found
                           if ro.is_overdue(a, today=today)},
        procesos={p.id_proceso: p.proceso for p in _processes(actor)},
        personas=people.names(db.session, actor, owned),
        mapa=_heat_map(actor, {}) if kind is RISKS else None,
        bandas=ro.LEVEL_RANGES.items(),
    )


def _export_pdf(kind: Kind) -> ResponseReturnValue:
    """The whole register as a PDF; one that cannot be rendered returns to the list."""
    today = local_today()
    context = _report(kind, current_actor(), today)
    try:
        pdf = render_pdf('riesgos/pdf_template.html', **context)
    except Exception:
        current_app.logger.exception('Error generating PDF for the register %s',
                                     kind.labels['file'])
        flash(kind.labels['pdf_error'], 'danger')
        return redirect(url_for(f'{kind.bp.name}.list_records'))
    return pdf_response(pdf, f"{kind.labels['file']}_{today:%Y-%m-%d}.pdf")


# -- a record --------------------------------------------------------------------------


def _show(kind: Kind, record_id: int) -> ResponseReturnValue:
    """A record's page: its fields, levels and actions."""
    actor = current_actor()
    registro = kind.register.get(db.session, actor, record_id)
    acciones = ro.actions.list_(db.session, actor, **{kind.parent_key: record_id})
    today = local_today()
    return render_template(
        'riesgos/detail.html',
        kind=kind,
        registro=registro,
        acciones=acciones,
        vencidas={a.id for a in acciones if ro.is_overdue(a, today=today)},
        revision_vencida=ro.revision_vencida(registro, today=today),
        abierto=ro.is_open(registro),
        proceso=_name(process_operations.get, actor, registro.proceso_id, 'proceso'),
        parte=_name(stakeholders.get, actor, registro.parte_interesada_id, 'nombre'),
        cuestion=_name(context_issues.issues.get, actor, registro.cuestion_id, 'titulo'),
        personas=people.names(db.session, actor, (
            registro.responsable_id, *(a.responsable_id for a in acciones))),
        cambios=citing_changes(actor, 'riesgo' if kind is RISKS else 'oportunidad', record_id),
    )


def _record_form(kind: Kind, actor: Actor, registro: Any = None) -> Any:
    """The record form with its interested party, context issue, process and owner pickers."""
    form = kind.form(obj=registro)
    form.parte_interesada_id.choices = party_choices(stakeholders.list_(db.session, actor))
    form.cuestion_id.choices = issue_choices(context_issues.issues.list_(db.session, actor),
                                             include=getattr(registro, 'cuestion_id', None))
    form.proceso_id.choices = process_choices(_processes(actor))
    form.responsable_id.choices = person_choices(
        people.choices(db.session, actor, include=getattr(registro, 'responsable_id', None)),
        empty=CHOOSE_PERSON)
    return form


def _new(kind: Kind) -> ResponseReturnValue:
    """Create a record; the identification date starts on today."""
    actor = current_actor()
    form = _record_form(kind, actor)
    if request.method == 'GET':
        form.fecha_identificacion.data = local_today()
    if form.validate_on_submit() and (created := saved(lambda: kind.register.create(
            db.session, actor, form_data(form, kind.fields), today=local_today()))):
        flash(kind.labels['created'].format(created.titulo), 'success')
        return _page(kind, created.id)
    return render_template('riesgos/form.html', kind=kind, form=form, registro=None)


def _edit(kind: Kind, record_id: int) -> ResponseReturnValue:
    actor = current_actor()
    registro = kind.register.get(db.session, actor, record_id)
    form = _record_form(kind, actor, registro)
    if form.validate_on_submit() and saved(lambda: kind.register.update(
            db.session, actor, record_id, form_data(form, kind.fields), today=local_today())):
        flash(kind.labels['updated'], 'success')
        return _page(kind, record_id)
    return render_template('riesgos/form.html', kind=kind, form=form, registro=registro)


def _delete(kind: Kind, record_id: int) -> ResponseReturnValue:
    """Delete a record without actions (administrators)."""
    if committed(lambda: kind.register.delete(db.session, current_actor(), record_id)):
        flash(kind.labels['deleted'], 'success')
        return redirect(url_for(f'{kind.bp.name}.list_records'))
    return _page(kind, record_id)


# -- actions ---------------------------------------------------------------------------


def _action(kind: Kind, actor: Actor, record_id: int, action_id: int) -> AccionRiesgo:
    """An action of the record; one of another record is a 404."""
    found = ro.actions.get(db.session, actor, action_id)
    if getattr(found, kind.parent_key) != record_id:
        raise NotFound(ro.ACTION_NOT_FOUND)
    return found


def _action_page(kind: Kind, registro: Any, form: Any, accion: AccionRiesgo | None,
                 titulo: str, boton: str, evaluacion: bool = False) -> str:
    return render_template('riesgos/action_form.html', kind=kind, registro=registro, form=form,
                           accion=accion, titulo=titulo, boton=boton, evaluacion=evaluacion)


def _action_form(actor: Actor, accion: AccionRiesgo | None = None) -> AccionRiesgoForm:
    """The action form; the owner picker keeps the stored one even when inactive."""
    form = AccionRiesgoForm(obj=accion)
    form.responsable_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(accion, 'responsable_id', None)), empty=CHOOSE_PERSON)
    return form


def _new_action(kind: Kind, record_id: int) -> ResponseReturnValue:
    """Add an action to an open record."""
    actor = current_actor()
    registro = kind.register.get(db.session, actor, record_id)
    if not ro.is_open(registro):
        flash(kind.closed, 'danger')
        return _page(kind, record_id)
    form = _action_form(actor)
    if form.validate_on_submit() and saved(lambda: ro.actions.create(
            db.session, actor, form_data(form, ACTION_FIELDS) | {kind.parent_key: record_id},
            today=local_today())):
        flash('Acción añadida.', 'success')
        return _page(kind, record_id)
    return _action_page(kind, registro, form, None, 'Nueva acción', 'Añadir acción')


def _edit_action(kind: Kind, record_id: int, action_id: int) -> ResponseReturnValue:
    """Edit an action and its follow-up (state and completion date)."""
    actor = current_actor()
    accion = _action(kind, actor, record_id, action_id)
    registro = kind.register.get(db.session, actor, record_id)
    form = _action_form(actor, accion)
    if form.validate_on_submit() and saved(lambda: ro.actions.update(
            db.session, actor, action_id, form_data(form, ACTION_FIELDS), today=local_today())):
        flash('Acción actualizada.', 'success')
        return _page(kind, record_id)
    return _action_page(kind, registro, form, accion, 'Editar la acción', 'Guardar')


def _evaluate_action(kind: Kind, record_id: int, action_id: int) -> ResponseReturnValue:
    """Evaluate a completed action's effectiveness (6.1.2 b); the date starts on today."""
    actor = current_actor()
    accion = _action(kind, actor, record_id, action_id)
    if accion.estado is not EstadoAccionRiesgo.completada:
        flash(ro.EVALUATION_NOT_DONE, 'danger')
        return _page(kind, record_id)
    registro = kind.register.get(db.session, actor, record_id)
    form = EficaciaAccionForm(obj=accion)
    if request.method == 'GET' and accion.fecha_evaluacion is None:
        form.fecha_evaluacion.data = local_today()
    if form.validate_on_submit() and saved(lambda: ro.actions.update(
            db.session, actor, action_id, form_data(form, EVALUATION_FIELDS),
            today=local_today())):
        flash('Eficacia de la acción evaluada.', 'success')
        return _page(kind, record_id)
    return _action_page(kind, registro, form, accion, 'Evaluar la eficacia',
                        'Guardar la evaluación', evaluacion=True)


def _delete_action(kind: Kind, record_id: int, action_id: int) -> ResponseReturnValue:
    """Delete an action (administrators)."""
    actor = current_actor()
    _action(kind, actor, record_id, action_id)
    if committed(lambda: ro.actions.delete(db.session, actor, action_id)):
        flash('Acción eliminada.', 'success')
    return _page(kind, record_id)


# -- the routes ------------------------------------------------------------------------


def _routes(kind: Kind) -> None:
    """Register the same views under ``kind``'s blueprint (endpoints by view name)."""
    bp = kind.bp

    def guarded(action: str, view: Callable) -> Callable:
        return login_required(require_permission(action, RESOURCE)(view))

    action = '/<int:record_id>/acciones/<int:action_id>'
    for rule, endpoint, permission, methods, view in (
        ('/', 'list_records', 'read', ['GET'], lambda: _list(kind)),
        ('/pdf', 'export_pdf', 'read', ['GET'], lambda: _export_pdf(kind)),
        ('/<int:record_id>', 'show_record', 'read', ['GET'],
         lambda record_id: _show(kind, record_id)),
        (f'/{kind.new_path}', 'new_record', 'create', ['GET', 'POST'], lambda: _new(kind)),
        ('/<int:record_id>/editar', 'edit_record', 'update', ['GET', 'POST'],
         lambda record_id: _edit(kind, record_id)),
        ('/<int:record_id>/eliminar', 'delete_record', 'delete', ['POST'],
         lambda record_id: _delete(kind, record_id)),
        ('/<int:record_id>/acciones/nueva', 'new_action', 'create', ['GET', 'POST'],
         lambda record_id: _new_action(kind, record_id)),
        (f'{action}/editar', 'edit_action', 'update', ['GET', 'POST'],
         lambda record_id, action_id: _edit_action(kind, record_id, action_id)),
        (f'{action}/eficacia', 'evaluate_action', 'update', ['GET', 'POST'],
         lambda record_id, action_id: _evaluate_action(kind, record_id, action_id)),
        (f'{action}/eliminar', 'delete_action', 'delete', ['POST'],
         lambda record_id, action_id: _delete_action(kind, record_id, action_id)),
    ):
        bp.add_url_rule(rule, endpoint, guarded(permission, view), methods=methods)


_routes(RISKS)
_routes(OPPORTUNITIES)
