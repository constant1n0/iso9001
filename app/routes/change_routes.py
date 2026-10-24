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

"""QMS change screens (ISO 9001 clause 6.3): adapters over ``qms_changes``.

Task CP-3 of ``change-planning``, under the policy resource ``qms_changes``:
every role reads; administrators and auditors create and edit changes and their
tasks; administrators delete a proposed change (decision CP5). Changes live
under ``/cambios`` (blueprint ``changes``):

- The list, the latest proposed first and paged (``PER_PAGE``), filters by
  state, origin, owner, process, ``fuera_de_plazo`` (approved and past its
  planned date), ``tareas_vencidas`` (with an overdue task) and a text in the
  title or the description; a value that names nothing is ignored, so it
  neither filters nor counts, and the page links keep the filters. Each change
  shows its origin with the record behind it, its owner, its state, its planned
  date with an overdue badge and its open tasks.
- A change's page shows its plan (description, origin and the record behind it,
  linked for the roles that read that record, owner, dates and communication),
  the 6.3 considerations a) to d), its processes, the documents the role may
  read (an operativo reads only documents in force, like ``documents``), its
  tasks with an overdue badge and the data of each stage. It offers the
  transitions each ``may_*`` guard allows for the change.
- The form shows only what the state lets change (CP3): every field while
  proposed; owner, planned date, communication, processes and documents once
  approved; nothing afterwards, so asking for it returns to the page with the
  reason. The origin record picker groups the records by origin (review actions
  of type ``cambio_sgc`` and context issues in force only, plus the one the
  change names) and the form refuses a record of another origin; every link is
  posted, so changing the origin clears the old one. ``?origen=`` and a link
  column (``?accion_revision_id=``...) preselect them, which the review's
  «Planificar cambio» uses.
- Every state change is a POST from the change's page, on the web only: each
  route checks its guard first (``PermissionDenied`` otherwise) and the service
  the rest.
- Tasks live under their change (one of another change is a 404) and are
  written only while it is proposed or approved.
- ``citing_changes`` gives the origin pages the changes a record gives rise to.

A service ``ValidationError`` or ``Conflict`` is rolled back: on a form it shows
on its field or is flashed, with the input kept; on a one-button action it is
flashed on the change's page. Dates come from ``local_today``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from typing import Any, TypeVar

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (
    CHOOSE_PERSON, NO_ORIGIN_RECORD, CambioSGCForm, CancelacionCambioForm, EficaciaCambioForm,
    ImplantacionCambioForm, TareaCambioForm, person_choices,
)
from ..models import (
    CHANGE_LINKS, CambioSGC, EstadoCambio, EstadoCuestion, OrigenCambio, ProcesoOperacion,
    TareaCambio,
)
from ..services import (
    audits, context_issues, crud, documents, nonconformities, people, process_operations,
    risks,
)
from ..services import management_reviews as mr
from ..services import qms_changes as qc
from ..services.actor import Actor
from ..services.errors import Conflict, NotFound, PermissionDenied, ValidationError
from ..utils.permissions import can, require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data

bp = Blueprint('changes', __name__, url_prefix='/cambios')

RESOURCE = 'qms_changes'
PER_PAGE = 20
T = TypeVar('T')
CHANGE_FIELDS = ('titulo', 'descripcion', 'origen', 'proposito', 'consecuencias', 'integridad',
                 'recursos', 'responsabilidades', 'comunicacion', 'responsable_id',
                 'fecha_propuesta', 'fecha_prevista', 'procesos', 'documentos')
TASK_FIELDS = ('descripcion', 'responsable_id', 'fecha_limite', 'estado', 'fecha_completada',
               'resultado')
LINK_FIELD = 'registro_origen'  # ``<link column>:<id>``, or empty
FLAGS = {'1': True, '0': False}
YES_NO = [('1', 'Sí'), ('0', 'No')]
LABEL_MAX = 90
# The service refusals shown on their field instead of flashed.
CHANGE_FIELD_ERRORS = {
    qc.PROPOSED_IN_FUTURE: 'fecha_propuesta',
    qc.PLANNED_BEFORE_PROPOSAL: 'fecha_prevista',
    qc.PLANNED_DATE_KEPT: 'fecha_prevista',
    qc.REVIEW_ACTION_NOT_A_CHANGE: LINK_FIELD,
    qc.ISSUE_SUPERSEDED: LINK_FIELD,
}
TASK_FIELD_ERRORS = {
    qc.COMPLETION_REQUIRED: 'fecha_completada',
    qc.COMPLETION_ONLY_WHEN_DONE: 'fecha_completada',
    qc.COMPLETION_IN_FUTURE: 'fecha_completada',
}


def _short(text: str | None) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= LABEL_MAX else f'{text[:LABEL_MAX - 1]}…'


@dataclass(frozen=True)
class OriginRecord:
    """The record behind a change through one link column (``CHANGE_LINKS``).

    ``resource`` is the read grant that shows and links it; ``attribute`` the
    change's relationship; ``label`` and ``url`` describe and open the record.
    """

    resource: str
    attribute: str
    label: Callable[[Any], str]
    url: Callable[[Any], str]


ORIGIN_RECORDS: dict[str, OriginRecord] = {
    'accion_revision_id': OriginRecord(
        'management_review', 'accion_revision', lambda a: _short(a.descripcion),
        lambda a: url_for('revision_direccion.show_review', review_id=a.revision_id,
                          _anchor=f'accion-{a.id}')),
    'no_conformidad_id': OriginRecord(
        'nonconformities', 'no_conformidad', lambda nc: f'N.º {nc.id} · {_short(nc.descripcion)}',
        lambda nc: url_for('no_conformidad.ver_no_conformidad', id=nc.id)),
    'auditoria_id': OriginRecord(
        'audits', 'auditoria', lambda a: f'{a.area_auditada} ({a.fecha:%d/%m/%Y})',
        lambda a: url_for('auditoria.ver_auditoria', id=a.id)),
    'riesgo_id': OriginRecord(
        'risks_opportunities', 'riesgo', lambda r: _short(r.titulo),
        lambda r: url_for('riesgo.show_record', record_id=r.id)),
    'oportunidad_id': OriginRecord(
        'risks_opportunities', 'oportunidad', lambda o: _short(o.titulo),
        lambda o: url_for('oportunidad.show_record', record_id=o.id)),
    'cuestion_id': OriginRecord(
        'organization_context', 'cuestion', lambda i: _short(i.titulo),
        lambda i: url_for('contexto.show_issue', issue_id=i.id)),
}


def _page(change_id: int) -> ResponseReturnValue:
    return redirect(url_for('changes.show_change', change_id=change_id))


def _members(enum_cls: type) -> list[tuple[str, str]]:
    return [(member.name, member.value) for member in enum_cls]


def _processes(actor: Actor) -> list[ProcesoOperacion]:
    """Every process, by name, for the picker and the filter."""
    return sorted(process_operations.list_(db.session, actor),
                  key=lambda process: (process.proceso.casefold(), process.id_proceso))


def _written(form: Any, write: Callable[[], T], on_field: dict[str, str]) -> T | None:
    """Run a service write and commit it; a refusal is rolled back and shown.

    A refusal ``on_field`` names goes on that field when the form has it; any
    other is flashed. Returns the written record, or ``None`` after a refusal.
    """
    try:
        written = write()
        db.session.commit()
    except (ValidationError, Conflict) as error:
        db.session.rollback()
        if (name := on_field.get(error.message)) is not None and name in form:
            form[name].errors.append(error.message)
        else:
            flash(error.message, 'danger')
        return None
    return written


def _valid(form: Any) -> bool:
    """Validate a one-button transition form; flash each error otherwise."""
    if form.validate_on_submit():
        return True
    for field in form:
        for error in field.errors:
            flash(f'{field.label.text}: {error}', 'danger')
    return False


# -- the record behind a change --------------------------------------------------------


def _link(cambio: CambioSGC | None) -> tuple[str, int] | None:
    """The change's link column and the id it names, or ``None``."""
    for column in CHANGE_LINKS:
        if (value := getattr(cambio, column, None)) is not None:
            return column, value
    return None


def origin_of(cambio: CambioSGC) -> dict[str, Any] | None:
    """The record behind ``cambio`` as ``{label, url}``, or ``None`` when it names none.

    A role that does not read that record sees that there is one, never what.
    """
    if (link := _link(cambio)) is None:
        return None
    kind = ORIGIN_RECORDS[link[0]]
    record = getattr(cambio, kind.attribute)
    if record is None or not can('read', kind.resource):
        return {'label': 'Registro vinculado', 'url': None}
    return {'label': kind.label(record), 'url': kind.url(record)}


def _audits(actor: Actor) -> list[Any]:
    """Every audit, a page at a time (the service only pages them)."""
    found: list[Any] = []
    page = 1
    while True:
        batch, total = audits.list_page(db.session, actor, page=page,
                                        per_page=crud.MAX_PER_PAGE)
        found += batch
        if not batch or len(found) >= total:
            return found
        page += 1


def _offered(actor: Actor) -> dict[str, Callable[[], list[Any]]]:
    """The records each link may newly name: review actions proposing a change to the
    QMS and context issues in force only (CP1)."""
    return {
        'accion_revision_id': lambda: mr.actions.list_(db.session, actor, tipo='cambio_sgc'),
        'no_conformidad_id': lambda: nonconformities.list_(db.session, actor),
        'auditoria_id': lambda: _audits(actor),
        'riesgo_id': lambda: risks.risks.list_(db.session, actor),
        'oportunidad_id': lambda: risks.opportunities.list_(db.session, actor),
        'cuestion_id': lambda: context_issues.issues.list_(db.session, actor,
                                                           estado=EstadoCuestion.vigente),
    }


def _origin_choices(actor: Actor, cambio: CambioSGC | None) -> dict[str, list]:
    """The origin record picker: the empty choice, then the records grouped by origin.

    The record the change names stays offered (marked when it would no longer be).
    """
    current = _link(cambio)
    groups: dict[str, list] = {'': [NO_ORIGIN_RECORD]}
    for column, records in _offered(actor).items():
        kind, (origin, _) = ORIGIN_RECORDS[column], CHANGE_LINKS[column]
        if not can('read', kind.resource):
            continue
        found = records()
        choices = [(f'{column}:{record.id}', kind.label(record)) for record in found]
        if current is not None and current[0] == column and current[1] not in {
                record.id for record in found}:
            record = getattr(cambio, kind.attribute)
            choices.append((f'{column}:{record.id}', f'{kind.label(record)} (no elegible)'))
        if choices:
            groups[origin.value] = choices
    return groups


# -- the list --------------------------------------------------------------------------


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def list_changes() -> ResponseReturnValue:
    """One page of the changes matching the filters; ``applied`` counts the filters used."""
    actor = current_actor()
    today = local_today()
    owners = people.names(db.session, actor, (
        change.responsable_id for change in qc.changes.list_(db.session, actor)))
    fields: list[tuple[str, str, list[tuple[str, str]], Callable[[str], Any]]] = [
        ('estado', 'Estado', _members(EstadoCambio), str),
        ('origen', 'Origen', _members(OrigenCambio), str),
        ('responsable_id', 'Responsable',
         [(str(person_id), nombre) for person_id, nombre
          in sorted(owners.items(), key=lambda item: item[1].casefold())], int),
        ('proceso_id', 'Proceso', [(str(p.id_proceso), p.proceso) for p in _processes(actor)],
         int),
        ('fuera_de_plazo', 'Fuera de plazo', YES_NO, FLAGS.get),
        ('tareas_vencidas', 'Con tareas vencidas', YES_NO, FLAGS.get),
    ]
    chosen, selected = {}, {}
    for name, _, choices, value_of in fields:
        text = request.args.get(name, '')
        if text in {value for value, _ in choices}:
            selected[name], chosen[name] = text, value_of(text)
    if texto := request.args.get('texto', '').strip():
        selected['texto'] = chosen['texto'] = texto
    page = max(request.args.get('page', 1, type=int), 1)
    found, total = qc.changes.list_page(db.session, actor, page=page, per_page=PER_PAGE,
                                        today=today, **chosen)
    return render_template(
        'cambios/list.html', cambios=found, personas=owners,
        origenes={change.id: origin_of(change) for change in found},
        fuera={change.id for change in found if qc.fuera_de_plazo(change, today=today)},
        abiertas={change.id: sum(task.estado in qc.OPEN_TASK_STATES for task in change.tareas)
                  for change in found},
        campos=fields, elegidos=selected, texto=texto, applied=len(chosen),
        page=page, pages=ceil(total / PER_PAGE), total=total)


# -- a change --------------------------------------------------------------------------


def _visible_documents(actor: Actor) -> set[int]:
    """Ids of the documents ``actor`` may read (``documents``' rule: drafts only for
    administrators and auditors)."""
    if not can('read', 'documents'):
        return set()
    return {document.id for document in documents.list_(db.session, actor)}


@bp.route('/<int:change_id>', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def show_change(change_id: int) -> ResponseReturnValue:
    """A change's page: its plan, considerations, lists, tasks, stages and transitions."""
    actor = current_actor()
    today = local_today()
    cambio = qc.changes.get(db.session, actor, change_id)
    visible = _visible_documents(actor)
    may = {name: guard(actor, cambio) for name, guard in (
        ('aprobar', qc.may_approve), ('implantar', qc.may_implement),
        ('eficacia', qc.may_review_effectiveness), ('cancelar', qc.may_cancel))}
    return render_template(
        'cambios/detail.html', cambio=cambio, origen=origin_of(cambio),
        documentos=[document for document in cambio.documentos if document.id in visible],
        tareas=cambio.tareas,
        vencidas={task.id for task in cambio.tareas if qc.is_overdue(task, cambio, today=today)},
        fuera_de_plazo=qc.fuera_de_plazo(cambio, today=today),
        abierto=cambio.estado in qc.OPEN_STATES,
        personas=people.names(db.session, actor, (
            cambio.responsable_id, cambio.aprobado_por_id, cambio.revisor_eficacia_id,
            *(task.responsable_id for task in cambio.tareas))),
        puede=may,
        implantacion=ImplantacionCambioForm(fecha_implantacion=today),
        eficacia=EficaciaCambioForm(fecha_eficacia=today),
        cancelacion=CancelacionCambioForm())


def _document_label(document: Any) -> str:
    label = f'{document.code} · {document.title}'
    return f'{label} (retirado)' if document.withdrawn_at else label


def _change_form(actor: Actor, cambio: CambioSGC | None = None) -> CambioSGCForm:
    """The change form, with only the fields the state lets change (CP3).

    The owner picker offers active people and the stored one; the document
    picker the documents ``actor`` may read.
    """
    form = CambioSGCForm(obj=cambio)
    form.responsable_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(cambio, 'responsable_id', None)), empty=CHOOSE_PERSON)
    form.procesos.choices = [(p.id_proceso, p.proceso) for p in _processes(actor)]
    visible = documents.list_(db.session, actor)
    form.documentos.choices = [(document.id, _document_label(document)) for document in visible]
    if cambio is None or cambio.estado is EstadoCambio.propuesto:
        form.registro_origen.choices = _origin_choices(actor, cambio)
    if cambio is not None and request.method == 'GET':
        shown = {document.id for document in visible}
        form.procesos.data = cambio.proceso_ids
        form.documentos.data = [i for i in cambio.documento_ids if i in shown]
        link = _link(cambio)
        form.registro_origen.data = '' if link is None else f'{link[0]}:{link[1]}'
    if cambio is not None and cambio.estado is EstadoCambio.aprobado:
        for name in [name for name in form._fields if name != 'csrf_token'
                     and name not in qc.APPROVED_WRITABLE]:
            del form[name]
    return form


def _payload(form: CambioSGCForm, actor: Actor, cambio: CambioSGC | None) -> dict[str, Any]:
    """The service payload: the form's fields, every link when the picker is shown, and
    the stored documents ``actor`` may not read, which the picker could not offer."""
    data = form_data(form, [name for name in CHANGE_FIELDS if name in form])
    if LINK_FIELD in form:
        column, _, value = (form.registro_origen.data or '').partition(':')
        data |= {link: None for link in CHANGE_LINKS}
        if column:
            data[column] = int(value)
    if 'documentos' in data and cambio is not None:
        hidden = set(cambio.documento_ids) - _visible_documents(actor)
        data['documentos'] = sorted(set(data['documentos']) | hidden)
    return data


def _preselect(form: CambioSGCForm) -> None:
    """``?origen=`` and one link column (``?accion_revision_id=5``) preselect the origin
    and its record, when the picker offers that record."""
    if (origen := request.args.get('origen', '')) in OrigenCambio.__members__:
        form.origen.data = origen
    offered = {value for value, *_ in form.registro_origen.iter_choices()}
    for column, (origin, _) in CHANGE_LINKS.items():
        record_id = request.args.get(column, type=int)
        if record_id is not None and f'{column}:{record_id}' in offered:
            form.origen.data, form.registro_origen.data = origin.name, f'{column}:{record_id}'


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_change() -> ResponseReturnValue:
    """Propose a change; its proposal date starts on today."""
    actor = current_actor()
    form = _change_form(actor)
    if request.method == 'GET':
        form.fecha_propuesta.data = local_today()
        _preselect(form)
    if form.validate_on_submit() and (created := _written(form, lambda: qc.changes.create(
            db.session, actor, _payload(form, actor, None), today=local_today()),
            CHANGE_FIELD_ERRORS)):
        flash(f'Cambio «{created.titulo}» propuesto.', 'success')
        return _page(created.id)
    return render_template('cambios/form.html', form=form, cambio=None)


@bp.route('/<int:change_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_change(change_id: int) -> ResponseReturnValue:
    """Edit what the change's state lets change; a closed change returns to its page."""
    actor = current_actor()
    cambio = qc.changes.get(db.session, actor, change_id)
    if cambio.estado in qc.READ_ONLY:
        flash(qc.READ_ONLY[cambio.estado], 'danger')
        return _page(change_id)
    form = _change_form(actor, cambio)
    if form.validate_on_submit() and _written(form, lambda: qc.changes.update(
            db.session, actor, change_id, _payload(form, actor, cambio), today=local_today()),
            CHANGE_FIELD_ERRORS):
        flash('Cambio actualizado.', 'success')
        return _page(change_id)
    return render_template('cambios/form.html', form=form, cambio=cambio)


@bp.route('/<int:change_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', RESOURCE)
def delete_change(change_id: int) -> ResponseReturnValue:
    """Delete a proposed change with its tasks (administrators)."""
    if committed(lambda: qc.changes.delete(db.session, current_actor(), change_id)):
        flash('Cambio eliminado.', 'success')
        return redirect(url_for('changes.list_changes'))
    return _page(change_id)


# -- the transitions (decision CP2), web only ------------------------------------------


def _guarded(may: Callable[[Actor], bool]) -> Actor:
    """The acting user when ``may`` lets them make the transition, else ``PermissionDenied``."""
    actor = current_actor()
    if not may(actor):
        raise PermissionDenied()
    return actor


@bp.route('/<int:change_id>/aprobar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def approve_change(change_id: int) -> ResponseReturnValue:
    """Approve a proposed change today (administrators)."""
    actor = _guarded(qc.may_approve)
    if committed(lambda: qc.approve(db.session, actor, change_id, today=local_today())):
        flash('Cambio aprobado.', 'success')
    return _page(change_id)


@bp.route('/<int:change_id>/implantar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def implement_change(change_id: int) -> ResponseReturnValue:
    """Record the implementation of an approved change (administrators and auditors)."""
    actor = _guarded(qc.may_implement)
    form = ImplantacionCambioForm()
    if _valid(form) and committed(lambda: qc.implement(
            db.session, actor, change_id, fecha=form.fecha_implantacion.data,
            today=local_today())):
        flash('Implantación del cambio registrada.', 'success')
    return _page(change_id)


@bp.route('/<int:change_id>/eficacia', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def review_change(change_id: int) -> ResponseReturnValue:
    """Review an implemented change's effectiveness (administrators and auditors)."""
    actor = _guarded(qc.may_review_effectiveness)
    form = EficaciaCambioForm()
    if _valid(form) and committed(lambda: qc.review_effectiveness(
            db.session, actor, change_id, eficacia=form.eficacia.data,
            fecha=form.fecha_eficacia.data, comentario=form.comentario_eficacia.data,
            today=local_today())):
        flash('Eficacia del cambio revisada.', 'success')
    return _page(change_id)


@bp.route('/<int:change_id>/cancelar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def cancel_change(change_id: int) -> ResponseReturnValue:
    """Cancel a proposed or approved change today, stating why (administrators)."""
    actor = _guarded(qc.may_cancel)
    form = CancelacionCambioForm()
    if _valid(form) and committed(lambda: qc.cancel(
            db.session, actor, change_id, motivo=form.motivo_cancelacion.data,
            today=local_today())):
        flash('Cambio cancelado.', 'success')
    return _page(change_id)


# -- tasks (decision CP4) --------------------------------------------------------------


def _task(actor: Actor, change_id: int, task_id: int) -> TareaCambio:
    """A task of the change; one of another change is a 404."""
    found = qc.tasks.get(db.session, actor, task_id)
    if found.cambio_id != change_id:
        raise NotFound(qc.TASK_NOT_FOUND)
    return found


def _open(cambio: CambioSGC) -> bool:
    """Whether the change's tasks may be written; flashes why not."""
    if cambio.estado in qc.OPEN_STATES:
        return True
    flash(qc.TASKS_CLOSED.format(estado=cambio.estado.value), 'danger')
    return False


def _task_form(actor: Actor, tarea: TareaCambio | None = None) -> TareaCambioForm:
    """The task form; the owner picker keeps the stored one even when inactive."""
    form = TareaCambioForm(obj=tarea)
    form.responsable_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(tarea, 'responsable_id', None)), empty=CHOOSE_PERSON)
    return form


def _task_page(cambio: CambioSGC, form: TareaCambioForm, tarea: TareaCambio | None) -> str:
    return render_template('cambios/task_form.html', cambio=cambio, form=form, tarea=tarea)


@bp.route('/<int:change_id>/tareas/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_task(change_id: int) -> ResponseReturnValue:
    """Add a task to a proposed or approved change."""
    actor = current_actor()
    cambio = qc.changes.get(db.session, actor, change_id)
    if not _open(cambio):
        return _page(change_id)
    form = _task_form(actor)
    if form.validate_on_submit() and _written(form, lambda: qc.tasks.create(
            db.session, actor, form_data(form, TASK_FIELDS) | {'cambio_id': change_id},
            today=local_today()), TASK_FIELD_ERRORS):
        flash('Tarea añadida.', 'success')
        return _page(change_id)
    return _task_page(cambio, form, None)


@bp.route('/<int:change_id>/tareas/<int:task_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_task(change_id: int, task_id: int) -> ResponseReturnValue:
    """Edit a task and its follow-up (state, completion date and result)."""
    actor = current_actor()
    cambio = qc.changes.get(db.session, actor, change_id)
    tarea = _task(actor, change_id, task_id)
    if not _open(cambio):
        return _page(change_id)
    form = _task_form(actor, tarea)
    if form.validate_on_submit() and _written(form, lambda: qc.tasks.update(
            db.session, actor, task_id, form_data(form, TASK_FIELDS), today=local_today()),
            TASK_FIELD_ERRORS):
        flash('Tarea actualizada.', 'success')
        return _page(change_id)
    return _task_page(cambio, form, tarea)


@bp.route('/<int:change_id>/tareas/<int:task_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', RESOURCE)
def delete_task(change_id: int, task_id: int) -> ResponseReturnValue:
    """Delete a task of a proposed or approved change (administrators)."""
    actor = current_actor()
    _task(actor, change_id, task_id)
    if committed(lambda: qc.tasks.delete(db.session, actor, task_id)):
        flash('Tarea eliminada.', 'success')
    return _page(change_id)


# -- the origin pages ------------------------------------------------------------------


def citing_changes(actor: Actor, origen: str, record_id: int) -> list[CambioSGC] | None:
    """The changes that name the record as the one behind them, for its page.

    ``None`` without the read grant of the changes, which hides the list.
    """
    if not can('read', RESOURCE):
        return None
    return qc.for_origin(db.session, actor, origen, record_id)
