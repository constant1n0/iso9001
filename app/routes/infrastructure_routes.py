"""Thin infrastructure register and immutable maintenance-history web adapters."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import InfrastructureForm, InfrastructureMaintenanceForm, person_choices
from ..models import TipoInfraestructura
from ..services import crud, infrastructure, nonconformities, people, process_operations
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('infrastructure', __name__, url_prefix='/infraestructura')
RESOURCE = 'infrastructure'
MASTER_FIELDS = tuple(field.name for field in infrastructure.SPEC.fields) + ('procesos',)
ENTRY_FIELDS = tuple(field.name for field in infrastructure.MAINTENANCE_SPEC.fields
                     if field.name != 'elemento_id')


def _paging():
    return crud.page_bounds(request.args.get('page', 1, type=int),
                            request.args.get('per_page', 20, type=int))


@bp.get('/')
@login_required
@require_permission('read', RESOURCE)
def list_items():
    page, per_page = _paging()
    codigo = request.args.get('codigo', '')
    tipo = request.args.get('tipo', '')
    tipo = tipo if tipo in TipoInfraestructura.__members__ else ''
    retirado = request.args.get('retirado', '')
    retirado = retirado if retirado in ('0', '1') else ''
    proceso_id = request.args.get('proceso_id', type=int)
    processes = process_operations.list_(db.session, current_actor())
    if proceso_id not in {p.id_proceso for p in processes}:
        proceso_id = None
    items, total = infrastructure.items.list_page(db.session, current_actor(), codigo=codigo,
        tipo=tipo or None, retirado={'0': False, '1': True}.get(retirado), proceso_id=proceso_id,
        page=page, per_page=per_page)
    late = {item.id: infrastructure.is_overdue(item, today=local_today()) for item in items}
    return render_template('infrastructure/list.html', items=items, late=late, codigo=codigo,
        tipo=tipo, retirado=retirado, proceso_id=proceso_id, processes=processes,
        types=TipoInfraestructura, page=page, per_page=per_page, total=total)


def _master_form(item=None):
    form = InfrastructureForm(obj=item)
    form.responsable_id.choices = person_choices(people.choices(db.session, current_actor(),
        **({'include': item.responsable_id} if item else {})))
    form.procesos.choices = [(p.id_proceso, p.proceso)
                            for p in process_operations.list_(db.session, current_actor())]
    if item is not None and request.method == 'GET':
        form.tipo.data = item.tipo.name
        form.procesos.data = item.proceso_ids
    return form


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_item():
    form = _master_form()
    if form.validate_on_submit() and (item := saved(lambda: infrastructure.items.create(
            db.session, current_actor(), form_data(form, MASTER_FIELDS)))):
        flash('Elemento registrado.', 'success')
        return redirect(url_for('infrastructure.show_item', item_id=item.id))
    return render_template('infrastructure/form.html', form=form, title='Nuevo elemento')


@bp.get('/<int:item_id>')
@login_required
@require_permission('read', RESOURCE)
def show_item(item_id):
    item = infrastructure.items.get(db.session, current_actor(), item_id)
    page, per_page = _paging()
    entries, total = infrastructure.maintenance.list_page(db.session, current_actor(),
        elemento_id=item.id, page=page, per_page=per_page)
    owners = people.names(db.session, current_actor(), [item.responsable_id])
    return render_template('infrastructure/show.html', item=item, entries=entries, owners=owners,
        late=infrastructure.is_overdue(item, today=local_today()), page=page, per_page=per_page, total=total)


@bp.route('/<int:item_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_item(item_id):
    item = infrastructure.items.get(db.session, current_actor(), item_id)
    form = _master_form(item)
    if form.validate_on_submit() and saved(lambda: infrastructure.items.update(
            db.session, current_actor(), item.id, form_data(form, MASTER_FIELDS))):
        flash('Elemento actualizado.', 'success')
        return redirect(url_for('infrastructure.show_item', item_id=item.id))
    return render_template('infrastructure/form.html', form=form, title='Editar elemento')


@bp.post('/<int:item_id>/eliminar')
@login_required
@require_permission('delete', RESOURCE)
def delete_item(item_id):
    if committed(lambda: infrastructure.items.delete(db.session, current_actor(), item_id)):
        return redirect(url_for('infrastructure.list_items'))
    return redirect(url_for('infrastructure.show_item', item_id=item_id))


@bp.route('/<int:item_id>/mantenimientos/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_maintenance(item_id):
    item = infrastructure.items.get(db.session, current_actor(), item_id)
    form = InfrastructureMaintenanceForm()
    form.no_conformidad_id.choices = [('', '— Sin no conformidad —')] + [
        (nc.id, f'NC-{nc.id} · {nc.descripcion}')
        for nc in nonconformities.list_(db.session, current_actor())]
    if form.validate_on_submit() and saved(lambda: infrastructure.maintenance.create(
            db.session, current_actor(), {'elemento_id': item.id} | form_data(form, ENTRY_FIELDS),
            today=local_today())):
        flash('Mantenimiento registrado y próxima fecha actualizada.', 'success')
        return redirect(url_for('infrastructure.show_item', item_id=item.id))
    return render_template('infrastructure/form.html', form=form,
                           title='Registrar mantenimiento · ' + item.codigo)
