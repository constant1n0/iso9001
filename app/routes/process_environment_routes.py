"""Thin web adapters for process environment and immutable check evidence."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import ProcessEnvironmentForm, ProcessEnvironmentCheckForm, person_choices
from ..models import FactorAmbiente
from ..services import crud, people, process_environment, process_operations
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('process_environment', __name__, url_prefix='/ambiente')
RESOURCE = 'process_environment'
MASTER_FIELDS = tuple(field.name for field in process_environment.SPEC.fields)
ENTRY_FIELDS = tuple(field.name for field in process_environment.CHECK_SPEC.fields
                     if field.name != 'condicion_id')


def _paging():
    return crud.page_bounds(request.args.get('page', 1, type=int),
                            request.args.get('per_page', 20, type=int))


@bp.get('/')
@login_required
@require_permission('read', RESOURCE)
def list_items():
    page, per_page = _paging()
    factor = request.args.get('factor', '')
    factor = factor if factor in FactorAmbiente.__members__ else ''
    selected = {key: request.args.get(key, '') for key in ('retirado', 'vencida')}
    selected = {key: value if value in ('0', '1') else '' for key, value in selected.items()}
    processes = process_operations.list_(db.session, current_actor())
    proceso_id = request.args.get('proceso_id', type=int)
    if proceso_id not in {p.id_proceso for p in processes}:
        proceso_id = None
    items, total = process_environment.items.list_page(db.session, current_actor(),
        factor=factor or None, proceso_id=proceso_id, today=local_today(),
        **{key: {'0': False, '1': True}.get(value) for key, value in selected.items()},
        page=page, per_page=per_page)
    late = {item.id: process_environment.is_overdue(item, today=local_today()) for item in items}
    return render_template('process_environment/list.html', items=items, late=late, factor=factor,
        **selected, proceso_id=proceso_id, processes=processes, factors=FactorAmbiente,
        page=page, per_page=per_page, total=total)


def _master_form(item=None):
    form = ProcessEnvironmentForm(obj=item)
    form.responsable_id.choices = person_choices(people.choices(db.session, current_actor(),
        **({'include': item.responsable_id} if item else {})))
    form.proceso_id.choices = [(p.id_proceso, p.proceso)
                              for p in process_operations.list_(db.session, current_actor())]
    if item is not None and request.method == 'GET':
        form.factor.data = item.factor.name
    return form


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_item():
    form = _master_form()
    if form.validate_on_submit() and (item := saved(lambda: process_environment.items.create(
            db.session, current_actor(), form_data(form, MASTER_FIELDS)))):
        flash('Condición registrada.', 'success')
        return redirect(url_for('process_environment.show_item', item_id=item.id))
    return render_template('process_environment/form.html', form=form, title='Nueva condición de ambiente')


@bp.get('/<int:item_id>')
@login_required
@require_permission('read', RESOURCE)
def show_item(item_id):
    item = process_environment.items.get(db.session, current_actor(), item_id)
    page, per_page = _paging()
    entries, total = process_environment.checks.list_page(db.session, current_actor(),
        condicion_id=item.id, page=page, per_page=per_page)
    owners = people.names(db.session, current_actor(), [item.responsable_id])
    return render_template('process_environment/show.html', item=item, entries=entries, owners=owners,
        late=process_environment.is_overdue(item, today=local_today()),
        page=page, per_page=per_page, total=total)


@bp.route('/<int:item_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_item(item_id):
    item = process_environment.items.get(db.session, current_actor(), item_id)
    form = _master_form(item)
    if form.validate_on_submit() and saved(lambda: process_environment.items.update(
            db.session, current_actor(), item.id, form_data(form, MASTER_FIELDS))):
        flash('Condición actualizada.', 'success')
        return redirect(url_for('process_environment.show_item', item_id=item.id))
    return render_template('process_environment/form.html', form=form, title='Editar condición de ambiente')


@bp.post('/<int:item_id>/eliminar')
@login_required
@require_permission('delete', RESOURCE)
def delete_item(item_id):
    if committed(lambda: process_environment.items.delete(db.session, current_actor(), item_id)):
        return redirect(url_for('process_environment.list_items'))
    return redirect(url_for('process_environment.show_item', item_id=item_id))


@bp.route('/<int:item_id>/comprobaciones/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_check(item_id):
    item = process_environment.items.get(db.session, current_actor(), item_id)
    form = ProcessEnvironmentCheckForm()
    if form.validate_on_submit() and saved(lambda: process_environment.checks.create(
            db.session, current_actor(), {'condicion_id': item.id} | form_data(form, ENTRY_FIELDS),
            today=local_today())):
        flash('Comprobación registrada y próxima fecha actualizada.', 'success')
        return redirect(url_for('process_environment.show_item', item_id=item.id))
    return render_template('process_environment/form.html', form=form,
                           title='Registrar comprobación · ' + item.descripcion)
