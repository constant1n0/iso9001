"""Thin equipment and immutable calibration-history web adapters."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import EquipoMedicionForm, ComprobacionEquipoForm, person_choices
from ..services import crud, equipment, people
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('equipment', __name__, url_prefix='/equipos')
RESOURCE = 'measuring_equipment'
MASTER_FIELDS = tuple(field.name for field in equipment.EQUIPMENT_SPEC.fields)
CHECK_FIELDS = tuple(field.name for field in equipment.CHECK_SPEC.fields if field.name != 'equipo_id')
LABELS = dict(apto='Apto', no_apto='No apto · fuera de servicio',
              vencido='Vencido', sin_comprobar='Sin comprobar', retirado='Retirado')


def _paging():
    return crud.page_bounds(request.args.get('page', 1, type=int),
                            request.args.get('per_page', 20, type=int))


@bp.get('/')
@login_required
@require_permission('read', RESOURCE)
def list_equipment():
    page, per_page = _paging()
    code = request.args.get('codigo', '')
    items, total = equipment.equipment.list_page(
        db.session, current_actor(), codigo=code, page=page, per_page=per_page)
    states = {item.id: LABELS[equipment.status(db.session, item, today=local_today())] for item in items}
    return render_template('equipment/list.html', items=items, states=states,
                           codigo=code, page=page, per_page=per_page, total=total)


def _master_form(item=None):
    form = EquipoMedicionForm(obj=item)
    form.responsable_id.choices = person_choices(people.choices(
        db.session, current_actor(), **({'include': item.responsable_id} if item else {})))
    return form


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_equipment():
    form = _master_form()
    if form.validate_on_submit() and (item := saved(lambda: equipment.equipment.create(
            db.session, current_actor(), form_data(form, MASTER_FIELDS)))):
        flash('Equipo registrado.', 'success')
        return redirect(url_for('equipment.show_equipment', equipment_id=item.id))
    return render_template('equipment/form.html', form=form, title='Nuevo equipo')


@bp.get('/<int:equipment_id>')
@login_required
@require_permission('read', RESOURCE)
def show_equipment(equipment_id):
    item = equipment.equipment.get(db.session, current_actor(), equipment_id)
    page, per_page = _paging()
    checks, total = equipment.checks.list_page(db.session, current_actor(), equipo_id=item.id,
                                               page=page, per_page=per_page)
    owners = people.names(db.session, current_actor(), [item.responsable_id])
    return render_template('equipment/show.html', item=item, checks=checks,
        state=LABELS[equipment.status(db.session, item, today=local_today())],
        owners=owners, page=page, per_page=per_page, total=total)


@bp.route('/<int:equipment_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_equipment(equipment_id):
    item = equipment.equipment.get(db.session, current_actor(), equipment_id)
    form = _master_form(item)
    if form.validate_on_submit() and saved(lambda: equipment.equipment.update(
            db.session, current_actor(), item.id, form_data(form, MASTER_FIELDS))):
        flash('Equipo actualizado.', 'success')
        return redirect(url_for('equipment.show_equipment', equipment_id=item.id))
    return render_template('equipment/form.html', form=form, title='Editar equipo')


@bp.post('/<int:equipment_id>/eliminar')
@login_required
@require_permission('delete', RESOURCE)
def delete_equipment(equipment_id):
    if committed(lambda: equipment.equipment.delete(db.session, current_actor(), equipment_id)):
        return redirect(url_for('equipment.list_equipment'))
    return redirect(url_for('equipment.show_equipment', equipment_id=equipment_id))


@bp.route('/<int:equipment_id>/comprobaciones/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_check(equipment_id):
    item = equipment.equipment.get(db.session, current_actor(), equipment_id)
    form = ComprobacionEquipoForm()
    if form.validate_on_submit() and saved(lambda: equipment.checks.create(
            db.session, current_actor(), {'equipo_id': item.id} | form_data(form, CHECK_FIELDS),
            today=local_today())):
        flash('Comprobación registrada. El estado del equipo se ha recalculado.', 'success')
        return redirect(url_for('equipment.show_equipment', equipment_id=item.id))
    return render_template('equipment/form.html', form=form,
                           title='Registrar comprobación · ' + item.codigo)
