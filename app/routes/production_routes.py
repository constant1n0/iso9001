"""Spanish PS1 screens; CSRF-protected lifecycle and strict submitted-field boundary."""
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required
from sqlalchemy import or_, select

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import BaseForm, DesignTransitionForm, ProductionOrderForm, person_choices
from ..models import (DesignApprovalState, DesignOutput, DesignOutputVersion, EquipoMedicion,
                      Person, ProductionKind, ProductionState)
from ..services import clientes, design_io, equipment, people, process_operations, production, qms_scope
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .design_routes import _control_payload_refused
from .people_routes import form_data, saved

bp = Blueprint('production', __name__, url_prefix='/produccion')


def _applicability() -> dict | None:
    scope = qms_scope.current(db.session, current_actor())
    values = None if scope is None else qms_scope.applicability_of(db.session, scope.id)
    return None if values is None else {code: values[code] for code in ('8.5.1', '8.5.2')}


def _choices() -> dict:
    who = current_actor()
    return {'state': [(m.name, m.value) for m in ProductionState],
            'kind': [(m.name, m.value) for m in ProductionKind],
            'process_id': [(str(p.id_proceso), p.proceso) for p in process_operations.list_(db.session, who)],
            'customer_id': [(str(c.id), c.nombre) for c in clientes.list_(db.session, who)]}


@bp.route('/')
@login_required
@require_permission('read', 'production')
def list_orders() -> ResponseReturnValue:
    """Ignore unknown selector filters, as the other register screens do."""
    choices = _choices()
    selected = {key: value for key in choices if (value := request.args.get(key, ''))
                in {v for v, _ in choices[key]}}
    filters = {key: int(value) if key.endswith('_id') else value for key, value in selected.items()}
    selected['lot'] = request.args.get('lot', '')
    return render_template('production/list.html', choices=choices, selected=selected,
        orders=production.orders.list_(db.session, current_actor(), **filters, lot=selected['lot']),
        applicability=_applicability())


@bp.route('/<int:order_id>')
@login_required
@require_permission('read', 'production')
def show_order(order_id: int) -> ResponseReturnValue:
    """Read exact conditions with current suitability and neutral restricted citations."""
    who = current_actor()
    row = production.orders.get(db.session, who, order_id)
    labels = dict(design_io.revision_choices(db.session, who))
    return render_template('production/detail.html', order=row, applicability=_applicability(),
        revision_labels={rid: labels.get(rid, 'Revisión vinculada') for rid in row.revision_ids},
        suitability={item.id: equipment.status(db.session, item, today=local_today()) for item in row.equipment},
        targets=production.TRANSITIONS.get(row.state, ()), transition_form=DesignTransitionForm(),
        editable=row.state in production.TRANSITIONS)


def _form(row: Any = None) -> ProductionOrderForm:
    who = current_actor()
    form = ProductionOrderForm(obj=row)
    choices = _choices()
    form.process_id.choices = [('', '— Elige un proceso —'), *choices['process_id']]
    form.customer_id.choices = [('', '— Sin cliente —'), *choices['customer_id']]
    form.owner_id.choices = person_choices(people.choices(db.session, who, include=getattr(row, 'owner_id', None)))
    current = getattr(row, 'design_output_id', None)
    outputs = db.session.scalars(select(DesignOutput).join(DesignOutputVersion,
        DesignOutputVersion.output_id == DesignOutput.id).where(or_(
        DesignOutputVersion.state == DesignApprovalState.approved, DesignOutput.id == current))
        .order_by(DesignOutput.id))
    form.design_output_id.choices = [('', '— Sin diseño —'), *((o.id,
        f'#{o.lineage_id} · Versión {o.version} · {o.text[:80]}') for o in outputs)]
    revisions = dict(design_io.revision_choices(db.session, who))
    for rid in getattr(row, 'revision_ids', []):
        revisions.update(design_io.revision_choices(db.session, who, rid))
    form.revision_ids.choices = [(key, label) for key, label in revisions.items() if key != '']
    form.equipment_ids.choices = [(item.id, f'{item.codigo} · {item.nombre}') for item in
        db.session.scalars(select(EquipoMedicion).order_by(EquipoMedicion.id))
        if item.id in getattr(row, 'equipment_ids', []) or equipment.status(db.session, item, today=local_today()) == 'apto']
    form.person_ids.choices = [(p.id, p.nombre) for p in db.session.scalars(select(Person).where(
        or_(Person.activo.is_(True), Person.id.in_(getattr(row, 'person_ids', [])))).order_by(Person.id))]
    if request.method == 'GET':
        form.kind.data = row.kind.name if row else 'production'
        for key in production.LINKS:
            form[key].data = getattr(row, key, [])
    return form


def _refused(form: Any) -> bool:
    if request.method == 'POST':
        unknown = (set(request.form) | set(request.files)) - {field.name for field in form}
        if unknown:
            flash(f'Campos no permitidos: {", ".join(sorted(unknown))}.', 'danger')
            return True
    return _control_payload_refused(form, 0)


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'production')
def new_order() -> ResponseReturnValue:
    """Create an order through the same service as MCP."""
    return _edit(None)


@bp.route('/<int:order_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'production')
def edit_order(order_id: int) -> ResponseReturnValue:
    """Edit an order through the same service as MCP."""
    return _edit(order_id)


def _edit(order_id: int | None) -> ResponseReturnValue:
    """Create/edit via the same service as MCP; every selector round-trips exactly."""
    who = current_actor()
    row = None if order_id is None else production.orders.get(db.session, who, order_id)
    if row is not None and row.state not in production.TRANSITIONS:
        flash(production.READ_ONLY, 'danger')
        return redirect(url_for('production.show_order', order_id=order_id))
    form = _form(row)
    title = 'Nueva orden' if row is None else 'Editar orden'
    if _refused(form):
        return render_template('production/form.html', form=form, title=title), 400
    if form.validate_on_submit():
        data = form_data(form, tuple(f.name for f in production.SPEC.fields) + tuple(production.LINKS))
        result = saved(lambda: production.orders.create(db.session, who, data) if row is None
                       else production.orders.update(db.session, who, order_id, data))
        if result:
            return redirect(url_for('production.show_order', order_id=result.id))
    return render_template('production/form.html', form=form, title=title)


@bp.route('/<int:order_id>/transicion', methods=['POST'])
@login_required
@require_permission('update', 'production')
def transition_order(order_id: int) -> ResponseReturnValue:
    """Start, finish or cancel an order on the web."""
    return _action(order_id, deleting=False)


@bp.route('/<int:order_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'production')
def delete_order(order_id: int) -> ResponseReturnValue:
    """Delete a planned order on the web."""
    return _action(order_id, deleting=True)


def _action(order_id: int, *, deleting: bool) -> ResponseReturnValue:
    """Explicit web POST actions; service rechecks role and state under the row lock."""
    form = BaseForm() if deleting else DesignTransitionForm()
    if _refused(form):
        return redirect(url_for('production.show_order', order_id=order_id)), 400
    operation = (lambda: production.orders.delete(db.session, current_actor(), order_id)) if deleting else (
        lambda: production.transition(db.session, current_actor(), order_id, form.target.data,
                                      cancellation_reason=form.cancellation_reason.data))
    if form.validate_on_submit() and committed(operation):
        return redirect(url_for('production.list_orders' if deleting else 'production.show_order',
                                **({} if deleting else {'order_id': order_id})))
    return redirect(url_for('production.show_order', order_id=order_id))
