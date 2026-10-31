"""Customer screens (8.2): thin adapters over the customer service."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..extensions import db
from ..forms import ClienteForm
from ..services import clientes, crud
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('cliente', __name__, url_prefix='/clientes')
FORM_FIELDS = ('nombre', 'nif', 'contacto', 'email', 'telefono', 'notas')


@bp.get('/')
@login_required
@require_permission('read', 'customers')
def list_clientes():
    nombre = request.args.get('nombre', '').strip()
    page, per_page = crud.page_bounds(request.args.get('page', 1, type=int),
                                      request.args.get('per_page', crud.DEFAULT_PER_PAGE, type=int))
    found, total = clientes.list_page(db.session, current_actor(), nombre=nombre,
                                      page=page, per_page=per_page)
    return render_template('clientes/list.html', clientes=found, nombre=nombre,
                           page=page, per_page=per_page, total=total)


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'customers')
def new_cliente():
    form = ClienteForm()
    if form.validate_on_submit() and (created := saved(lambda: clientes.create(
            db.session, current_actor(), form_data(form, FORM_FIELDS)))):
        flash(f'Cliente «{created.nombre}» creado.', 'success')
        return redirect(url_for('cliente.show_cliente', customer_id=created.id))
    return render_template('clientes/form.html', form=form, customer=None)


@bp.get('/<int:customer_id>')
@login_required
@require_permission('read', 'customers')
def show_cliente(customer_id):
    found = clientes.get(db.session, current_actor(), customer_id)
    return render_template('clientes/show.html', customer=found)


@bp.route('/<int:customer_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'customers')
def edit_cliente(customer_id):
    found = clientes.get(db.session, current_actor(), customer_id)
    form = ClienteForm(obj=found)
    if form.validate_on_submit() and saved(lambda: clientes.update(
            db.session, current_actor(), customer_id, form_data(form, FORM_FIELDS))):
        flash('Cliente actualizado.', 'success')
        return redirect(url_for('cliente.show_cliente', customer_id=customer_id))
    return render_template('clientes/form.html', form=form, customer=found)


@bp.post('/<int:customer_id>/eliminar')
@login_required
@require_permission('delete', 'customers')
def delete_cliente(customer_id):
    if committed(lambda: clientes.delete(db.session, current_actor(), customer_id)):
        flash('Cliente eliminado.', 'success')
        return redirect(url_for('cliente.list_clientes'))
    return redirect(url_for('cliente.show_cliente', customer_id=customer_id))
