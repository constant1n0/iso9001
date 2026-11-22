"""Commercial requirements screens; strict forms and administrator-only decisions."""
from typing import Any
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required
from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (BaseForm, CustomerOrderForm, CustomerOrderRevisionForm,
    CustomerOrderReviewForm, CustomerOrderCancellationForm, ORDER_CHECKS)
from ..models import CustomerOrderKind, CustomerOrderStatus, RoleEnum
from ..services import clientes, customer_orders as service
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved
from .release_routes import _refuse

bp = Blueprint('customer_orders', __name__, url_prefix='/pedidos')


def _choices() -> dict:
    return {'status': [(m.name, m.value) for m in CustomerOrderStatus],
        'kind': [(m.name, m.value) for m in CustomerOrderKind],
        'customer_id': [(str(c.id), c.nombre) for c in clientes.list_(db.session, current_actor())]}


@bp.route('/')
@login_required
@require_permission('read', 'customer_order')
def list_orders() -> ResponseReturnValue:
    """Filter status, kind and customer; ignore unknown choices."""
    choices = _choices()
    selected = {k: v for k in choices if (v := request.args.get(k, '')) in dict(choices[k])}
    filters = {k: int(v) if k.endswith('_id') else v for k, v in selected.items()}
    return render_template('customer_orders/list.html', choices=choices, selected=selected,
        items=service.orders.list_(db.session, current_actor(), **filters))


@bp.route('/<int:order_id>')
@login_required
@require_permission('read', 'customer_order')
def show_order(order_id: int) -> ResponseReturnValue:
    """Read all requirements revisions and immutable review evidence."""
    who = current_actor()
    revisions = service.revisions.list_(db.session, who, order_id=order_id)
    return render_template('customer_orders/detail.html',
        item=service.orders.get(db.session, who, order_id), revisions=revisions,
        latest=revisions[0], checks=ORDER_CHECKS, action_form=BaseForm(),
        cancel_form=CustomerOrderCancellationForm(), administrator=who.role is RoleEnum.ADMINISTRADOR)


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'customer_order')
def new_order() -> ResponseReturnValue:
    """Start revision one together with its commercial identity."""
    return _edit(None)


@bp.route('/<int:order_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'customer_order')
def edit_order(order_id: int) -> ResponseReturnValue:
    """Maintain the first draft; later changes use a numbered revision."""
    return _edit(order_id)


def _submit(form: Any, operation: Any, order_id: int | None, title: str,
            readonly: bool = False) -> ResponseReturnValue:
    refused = _refuse(form)
    if readonly and not refused:
        flash(service.READ_ONLY, 'danger')
        return redirect(url_for('customer_orders.show_order', order_id=order_id))
    if not refused and form.validate_on_submit() and (row := saved(operation)):
        return redirect(url_for('customer_orders.show_order', order_id=getattr(row, 'order_id', row.id)))
    return render_template('customer_orders/form.html', form=form, title=title), 400 if refused else 200


def _edit(order_id: int | None) -> ResponseReturnValue:
    who = current_actor()
    row = None if order_id is None else service.orders.get(db.session, who, order_id)
    revision = None if row is None else service.latest(db.session, row.id)
    form = CustomerOrderForm(obj=row)
    form.customer_id.choices = [('', '— Elige un cliente —'), *_choices()['customer_id']]
    if request.method == 'GET':
        form.kind.data = row.kind.name if row else 'order'
        form.received_date.data = row.received_date if row else local_today()
        for key in service.CONTENT:
            form[key].data = getattr(revision, key, None)
    data = lambda: form_data(form, service.SPEC.writable)
    return _submit(form, lambda: service.orders.create(db.session, who, data(), today=local_today())
        if row is None else service.orders.update(db.session, who, row.id, data(), today=local_today()),
        order_id, 'Nuevo pedido o contrato' if row is None else 'Editar borrador',
        row is not None and (row.cancelled or revision.result is not None or revision.number != 1))


def _child(order_id: int, revision_id: int) -> Any:
    row = service.revisions.get(db.session, current_actor(), revision_id)
    if row.order_id != order_id:
        abort(404)
    return row


@bp.route('/<int:order_id>/revisiones/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'customer_order')
def new_revision(order_id: int) -> ResponseReturnValue:
    """Append requirements with mandatory change reason and recipients."""
    order = service.orders.get(db.session, current_actor(), order_id)
    previous = service.latest(db.session, order_id)
    form = CustomerOrderRevisionForm()
    if request.method == 'GET':
        for key in service.CONTENT[:3]:
            form[key].data = getattr(previous, key)
    return _submit(form, lambda: service.revisions.create(db.session, current_actor(),
        form_data(form, service.CONTENT) | {'order_id': order_id}), order_id,
        'Nueva revisión del pedido', order.cancelled or previous.result is None)


@bp.route('/<int:order_id>/revisiones/<int:revision_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'customer_order')
def edit_revision(order_id: int, revision_id: int) -> ResponseReturnValue:
    """Only the latest unreviewed revision is editable, even for no-op posts."""
    order = service.orders.get(db.session, current_actor(), order_id)
    row = _child(order_id, revision_id)
    form = CustomerOrderRevisionForm(obj=row)
    return _submit(form, lambda: service.revisions.update(db.session, current_actor(), row.id,
        form_data(form, service.CONTENT)), order_id, 'Editar requisitos',
        order.cancelled or row.result is not None or service.latest(db.session, order_id).id != row.id)


@bp.route('/<int:order_id>/revisiones/<int:revision_id>/revisar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'customer_order')
def review_revision(order_id: int, revision_id: int) -> ResponseReturnValue:
    """Administrator-only decision with no posted reviewer or server date."""
    row = _child(order_id, revision_id)
    if current_actor().role is not RoleEnum.ADMINISTRADOR:
        flash('Solo un administrador puede revisar pedidos.', 'danger')
        return redirect(url_for('customer_orders.show_order', order_id=order_id))
    form = CustomerOrderReviewForm()
    def operation() -> Any:
        data = form_data(form, service.REVIEW_SPEC.writable)
        for key in ('differences_resolved', 'undocumented'):
            data[key] = data[key] == 'true'
        return service.review(db.session, current_actor(), row.id, data, today=local_today())
    order = service.orders.get(db.session, current_actor(), order_id)
    return _submit(form, operation, order_id, 'Revisión de requisitos (8.2.3.1)',
        order.cancelled or row.result is not None or service.latest(db.session, order_id).id != row.id)


@bp.route('/<int:order_id>/cancelar', methods=['POST'])
@login_required
@require_permission('update', 'customer_order')
def cancel_order(order_id: int) -> ResponseReturnValue:
    """Cancel explicitly on the web; the service enforces administrator authority."""
    form = CustomerOrderCancellationForm()
    refused = _refuse(form)
    if not refused and form.validate_on_submit():
        committed(lambda: service.cancel(db.session, current_actor(), order_id, reason=form.reason.data))
    return redirect(url_for('customer_orders.show_order', order_id=order_id)), 400 if refused else 302


@bp.route('/<int:order_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'customer_order')
def delete_order(order_id: int) -> ResponseReturnValue:
    """Delete only a single unreviewed draft, including its audited revision."""
    form = BaseForm()
    refused = _refuse(form)
    if not refused and form.validate_on_submit() and committed(
            lambda: service.orders.delete(db.session, current_actor(), order_id)):
        return redirect(url_for('customer_orders.list_orders'))
    return redirect(url_for('customer_orders.show_order', order_id=order_id)), 400 if refused else 302
