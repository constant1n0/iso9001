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

"""External provider screens (ISO 9001 clause 8.4): adapters over ``providers``.

Task EP-2 of ``external-providers``. Providers live under ``/proveedores``
(blueprint ``proveedor``), each with its evaluations. Every role reads;
administrators and auditors create and edit providers and record and edit
evaluations; administrators delete and decide the state (policy
``EXTERNAL_PROVIDERS``, decisions EP2 and EP5).

- The list, by name, filters by state, supply type, process, linked processes
  (``con_procesos``, which the dashboard links), latest result, overdue
  evaluation and a name search; a value that names nothing is ignored, so it
  neither filters nor counts. Each provider shows its supply, its state, the
  latest result with that evaluation's average, the next evaluation with an
  overdue badge and its open and total nonconformities.
- A provider's page shows every field, its processes and nonconformities, each
  linked, the current decision (date, decider and reason; the audit log keeps
  the history), its performance and its evaluations. Administrators decide
  there: approve, condition or block, each with a reason (required to condition
  or block); the current state's decision is not offered.
- Evaluations live under their provider; one asked for under another provider
  is a 404. The evaluator picker offers active people, plus the stored one.
- A provider with evaluations or nonconformities offers no delete; the service
  refuses it anyway and the reason is flashed on its page.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed; a refused decision or delete is
flashed on the provider's page.
"""

from collections.abc import Callable
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import CHOOSE_PERSON, EvaluacionProveedorForm, ProveedorForm, person_choices
from ..models import (
    EstadoProveedor, EvaluacionProveedor, ProcesoOperacion, Proveedor, ResultadoEvaluacion,
    TipoSuministro,
)
from ..services import nonconformities, people, process_operations
from ..services import providers as pv
from ..services.actor import Actor
from ..services.errors import NotFound, PermissionDenied
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('proveedor', __name__, url_prefix='/proveedores')

RESOURCE = 'external_providers'
PROVIDER_FIELDS = ('nombre', 'nif', 'contacto', 'email', 'telefono', 'tipo_suministro',
                   'descripcion_suministro', 'procesos', 'criterios_seleccion',
                   'fecha_proxima_evaluacion', 'notas')
EVALUATION_FIELDS = ('fecha', 'evaluador_id', 'calidad', 'plazo', 'servicio', 'documentacion',
                     'comentario', 'fecha_proxima')
FLAGS = {'1': True, '0': False}
YES_NO = [('1', 'Sí'), ('0', 'No')]
# The administrators' decisions (EP2): path, the state it leads to, button and flash.
DECISIONS = (
    ('aprobar', EstadoProveedor.aprobado, 'Aprobar', 'Proveedor aprobado.'),
    ('condicionar', EstadoProveedor.condicionado, 'Condicionar', 'Proveedor condicionado.'),
    ('bloquear', EstadoProveedor.bloqueado, 'Bloquear', 'Proveedor bloqueado.'),
)


def _page(provider_id: int) -> ResponseReturnValue:
    return redirect(url_for('proveedor.show_provider', provider_id=provider_id))


def _members(enum_cls: type) -> list[tuple[str, str]]:
    return [(member.name, member.value) for member in enum_cls]


def _processes(actor: Actor) -> list[ProcesoOperacion]:
    """Every process, by name, for the picker and the filter."""
    return sorted(process_operations.list_(db.session, actor),
                  key=lambda process: (process.proceso.casefold(), process.id_proceso))


# -- the list --------------------------------------------------------------------------


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def list_providers() -> ResponseReturnValue:
    """Providers matching the filters with their figures; ``applied`` counts the filters."""
    actor = current_actor()
    today = local_today()
    fields: list[tuple[str, str, list[tuple[str, str]], Callable[[str], Any]]] = [
        ('estado', 'Estado', _members(EstadoProveedor), str),
        ('tipo_suministro', 'Tipo de suministro', _members(TipoSuministro), str),
        ('proceso_id', 'Proceso',
         [(str(p.id_proceso), p.proceso) for p in _processes(actor)], int),
        ('con_procesos', 'Con procesos', YES_NO, FLAGS.get),
        ('ultimo_resultado', 'Último resultado', _members(ResultadoEvaluacion), str),
        ('evaluacion_vencida', 'Evaluación vencida', YES_NO, FLAGS.get),
    ]
    chosen, selected = {}, {}
    for name, _, choices, value_of in fields:
        text = request.args.get(name, '')
        if text in {value for value, _ in choices}:
            selected[name], chosen[name] = text, value_of(text)
    if nombre := request.args.get('nombre', '').strip():
        chosen['nombre'] = nombre
    found = pv.providers.list_(db.session, actor, today=today, **chosen)
    return render_template(
        'proveedores/list.html', proveedores=found,
        desempeno=pv.performances(db.session, actor, [p.id for p in found]),
        vencidas={p.id for p in found if pv.evaluacion_vencida(p, today=today)},
        campos=fields, elegidos=selected, nombre=nombre, applied=len(chosen))


# -- a provider ------------------------------------------------------------------------


@bp.route('/<int:provider_id>', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def show_provider(provider_id: int) -> ResponseReturnValue:
    """A provider's page: its fields, decision, performance, evaluations and citations."""
    actor = current_actor()
    proveedor = pv.providers.get(db.session, actor, provider_id)
    evaluaciones = pv.evaluations.list_(db.session, actor, proveedor_id=provider_id)
    citadas = nonconformities.list_(db.session, actor, proveedor_id=provider_id)
    decisiones = [decision for decision in DECISIONS if decision[1] is not proveedor.estado]
    scored = [(found, pv.puntuacion(found)) for found in evaluaciones]
    return render_template(
        'proveedores/detail.html', proveedor=proveedor,
        evaluaciones=[(found, score, pv.resultado(score)) for found, score in scored],
        no_conformidades=citadas,
        desempeno=pv.performances(db.session, actor, [provider_id])[provider_id],
        vencida=pv.evaluacion_vencida(proveedor, today=local_today()),
        personas=people.names(db.session, actor, (
            proveedor.decidido_por_id, *(e.evaluador_id for e in evaluaciones))),
        decisiones=decisiones if pv.may_decide(actor) else [],
        eliminable=not evaluaciones and not citadas)


def _provider_form(actor: Actor, proveedor: Proveedor | None = None) -> ProveedorForm:
    """The provider form; the process picker shows the linked ones on edit."""
    form = ProveedorForm(obj=proveedor)
    form.procesos.choices = [(p.id_proceso, p.proceso) for p in _processes(actor)]
    if proveedor is not None and request.method == 'GET':
        form.procesos.data = proveedor.proceso_ids
    return form


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_provider() -> ResponseReturnValue:
    """Register a provider; it starts ``pendiente`` (EP2)."""
    actor = current_actor()
    form = _provider_form(actor)
    if form.validate_on_submit() and (created := saved(lambda: pv.providers.create(
            db.session, actor, form_data(form, PROVIDER_FIELDS)))):
        flash(f'Proveedor «{created.nombre}» creado.', 'success')
        return _page(created.id)
    return render_template('proveedores/form.html', form=form, proveedor=None)


@bp.route('/<int:provider_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_provider(provider_id: int) -> ResponseReturnValue:
    """Edit a provider; the processes picked replace the linked ones."""
    actor = current_actor()
    proveedor = pv.providers.get(db.session, actor, provider_id)
    form = _provider_form(actor, proveedor)
    if form.validate_on_submit() and saved(lambda: pv.providers.update(
            db.session, actor, provider_id, form_data(form, PROVIDER_FIELDS))):
        flash('Proveedor actualizado.', 'success')
        return _page(provider_id)
    return render_template('proveedores/form.html', form=form, proveedor=proveedor)


@bp.route('/<int:provider_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', RESOURCE)
def delete_provider(provider_id: int) -> ResponseReturnValue:
    """Delete a provider without evaluations or nonconformities (administrators)."""
    if committed(lambda: pv.providers.delete(db.session, current_actor(), provider_id)):
        flash('Proveedor eliminado.', 'success')
        return redirect(url_for('proveedor.list_providers'))
    return _page(provider_id)


@bp.route('/<int:provider_id>/<any(aprobar, condicionar, bloquear):decision>',
          methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def decide(provider_id: int, decision: str) -> ResponseReturnValue:
    """Approve, condition or block a provider today with a reason (administrators, EP2)."""
    actor = current_actor()
    if not pv.may_decide(actor):
        raise PermissionDenied()
    motivo, today = request.form.get('motivo', ''), local_today()
    calls = {
        'aprobar': lambda: pv.approve(db.session, actor, provider_id, today=today, motivo=motivo),
        'condicionar': lambda: pv.condition(db.session, actor, provider_id, motivo, today=today),
        'bloquear': lambda: pv.block(db.session, actor, provider_id, motivo, today=today),
    }
    if committed(calls[decision]):
        flash(next(done for path, _, _, done in DECISIONS if path == decision), 'success')
    return _page(provider_id)


# -- evaluations -----------------------------------------------------------------------


def _evaluation(actor: Actor, provider_id: int, evaluation_id: int) -> EvaluacionProveedor:
    """An evaluation of the provider; one of another provider is a 404."""
    found = pv.evaluations.get(db.session, actor, evaluation_id)
    if found.proveedor_id != provider_id:
        raise NotFound(pv.EVALUATION_NOT_FOUND)
    return found


def _evaluation_form(actor: Actor,
                     evaluacion: EvaluacionProveedor | None = None) -> EvaluacionProveedorForm:
    """The evaluation form; the evaluator picker offers active people and the stored one."""
    form = EvaluacionProveedorForm(obj=evaluacion)
    form.evaluador_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(evaluacion, 'evaluador_id', None)), empty=CHOOSE_PERSON)
    return form


def _evaluation_page(proveedor: Proveedor, form: EvaluacionProveedorForm,
                     evaluacion: EvaluacionProveedor | None) -> str:
    return render_template('proveedores/evaluation_form.html', proveedor=proveedor, form=form,
                           evaluacion=evaluacion)


@bp.route('/<int:provider_id>/evaluaciones/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_evaluation(provider_id: int) -> ResponseReturnValue:
    """Record an evaluation; its date starts on today."""
    actor = current_actor()
    proveedor = pv.providers.get(db.session, actor, provider_id)
    form = _evaluation_form(actor)
    if request.method == 'GET':
        form.fecha.data = local_today()
    if form.validate_on_submit() and saved(lambda: pv.evaluations.create(
            db.session, actor, form_data(form, EVALUATION_FIELDS) | {'proveedor_id': provider_id},
            today=local_today())):
        flash('Evaluación registrada.', 'success')
        return _page(provider_id)
    return _evaluation_page(proveedor, form, None)


@bp.route('/<int:provider_id>/evaluaciones/<int:evaluation_id>/editar',
          methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_evaluation(provider_id: int, evaluation_id: int) -> ResponseReturnValue:
    actor = current_actor()
    evaluacion = _evaluation(actor, provider_id, evaluation_id)
    proveedor = pv.providers.get(db.session, actor, provider_id)
    form = _evaluation_form(actor, evaluacion)
    if form.validate_on_submit() and saved(lambda: pv.evaluations.update(
            db.session, actor, evaluation_id, form_data(form, EVALUATION_FIELDS),
            today=local_today())):
        flash('Evaluación actualizada.', 'success')
        return _page(provider_id)
    return _evaluation_page(proveedor, form, evaluacion)


@bp.route('/<int:provider_id>/evaluaciones/<int:evaluation_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', RESOURCE)
def delete_evaluation(provider_id: int, evaluation_id: int) -> ResponseReturnValue:
    """Delete an evaluation (administrators)."""
    actor = current_actor()
    _evaluation(actor, provider_id, evaluation_id)
    if committed(lambda: pv.evaluations.delete(db.session, actor, evaluation_id)):
        flash('Evaluación eliminada.', 'success')
    return _page(provider_id)
