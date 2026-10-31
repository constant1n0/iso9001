"""Complaint screens and POST-only lifecycle transitions (9.1.2)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import ReclamacionForm
from ..models import CanalContacto, EstadoReclamacion
from ..services import clientes, crud, nonconformities, reclamaciones
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('reclamacion', __name__, url_prefix='/reclamaciones')
FORM_FIELDS = ('cliente_id', 'fecha_recepcion', 'descripcion', 'canal', 'no_conformidad_id')


def _page(complaint_id):
    return redirect(url_for('reclamacion.show_reclamacion', complaint_id=complaint_id))


def _form(actor, found=None):
    form = ReclamacionForm(obj=found)
    form.cliente_id.choices = [('', '— Elige un cliente —'),
                               *((c.id, c.nombre) for c in clientes.list_(db.session, actor))]
    form.no_conformidad_id.choices = [('', '— Sin no conformidad —'),
        *((nc.id, f'NC {nc.id} · {nc.descripcion[:80]}')
          for nc in nonconformities.list_(db.session, actor))]
    if found is None and request.method == 'GET':
        form.fecha_recepcion.data = local_today()
    return form


@bp.get('/')
@login_required
@require_permission('read', 'complaints')
def listar_reclamaciones():
    actor = current_actor()
    customers = clientes.list_(db.session, actor)
    chosen = {}
    customer_id = request.args.get('cliente_id', type=int)
    if customer_id in {c.id for c in customers}:
        chosen['cliente_id'] = customer_id
    for key, enum_cls in (('estado', EstadoReclamacion), ('canal', CanalContacto)):
        value = request.args.get(key, '')
        if value in enum_cls.__members__:
            chosen[key] = value
    page, per_page = crud.page_bounds(request.args.get('page', 1, type=int),
                                      request.args.get('per_page', crud.DEFAULT_PER_PAGE, type=int))
    found, total = reclamaciones.list_page(db.session, actor, **chosen, page=page, per_page=per_page)
    return render_template('reclamaciones/list.html', reclamaciones=found, clientes=customers,
                           estados=EstadoReclamacion, canales=CanalContacto, elegidos=chosen,
                           page=page, per_page=per_page, total=total)


@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'complaints')
def new_reclamacion():
    actor = current_actor()
    form = _form(actor)
    if form.validate_on_submit() and (created := saved(lambda: reclamaciones.create(
            db.session, actor, form_data(form, FORM_FIELDS), today=local_today()))):
        flash('Reclamación registrada.', 'success')
        return _page(created.id)
    return render_template('reclamaciones/form.html', form=form, reclamacion=None)


@bp.get('/<int:complaint_id>')
@login_required
@require_permission('read', 'complaints')
def show_reclamacion(complaint_id):
    actor = current_actor()
    found = reclamaciones.get(db.session, actor, complaint_id)
    return render_template('reclamaciones/show.html', reclamacion=found,
        editable=found.estado not in reclamaciones.TERMINAL_STATES,
        respondible=reclamaciones.may_respond(actor, found),
        cerrable=reclamaciones.may_close(actor, found),
        cancelable=reclamaciones.may_cancel(actor, found),
        reabrible=reclamaciones.may_reopen(actor, found))


@bp.route('/<int:complaint_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'complaints')
def edit_reclamacion(complaint_id):
    actor = current_actor()
    found = reclamaciones.get(db.session, actor, complaint_id)
    if found.estado in reclamaciones.TERMINAL_STATES:
        flash(reclamaciones.READ_ONLY_MESSAGE, 'warning')
        return _page(complaint_id)
    form = _form(actor, found)
    if form.validate_on_submit() and saved(lambda: reclamaciones.update(
            db.session, actor, complaint_id, form_data(form, FORM_FIELDS), today=local_today())):
        flash('Reclamación actualizada.', 'success')
        return _page(complaint_id)
    return render_template('reclamaciones/form.html', form=form, reclamacion=found)


@bp.post('/<int:complaint_id>/responder')
@login_required
@require_permission('update', 'complaints')
def respond(complaint_id):
    if committed(lambda: reclamaciones.respond(db.session, current_actor(), complaint_id,
            request.form.get('respuesta'), today=local_today())):
        flash('Respuesta registrada.', 'success')
    return _page(complaint_id)


@bp.post('/<int:complaint_id>/cerrar')
@login_required
@require_permission('update', 'complaints')
def close(complaint_id):
    if committed(lambda: reclamaciones.close(db.session, current_actor(), complaint_id,
                                             today=local_today())):
        flash('Reclamación cerrada.', 'success')
    return _page(complaint_id)


@bp.post('/<int:complaint_id>/cancelar')
@login_required
@require_permission('update', 'complaints')
def cancel(complaint_id):
    if committed(lambda: reclamaciones.cancel(db.session, current_actor(), complaint_id,
                                              request.form.get('motivo_cancelacion'))):
        flash('Reclamación cancelada.', 'success')
    return _page(complaint_id)


@bp.post('/<int:complaint_id>/reabrir')
@login_required
@require_permission('update', 'complaints')
def reopen(complaint_id):
    if committed(lambda: reclamaciones.reopen(db.session, current_actor(), complaint_id)):
        flash('Reclamación reabierta.', 'success')
    return _page(complaint_id)
