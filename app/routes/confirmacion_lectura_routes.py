"""Read confirmation screens (5.2, 7.3): thin adapters over the register service."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import ConfirmacionLecturaForm, person_choices, CHOOSE_PERSON
from ..models import DocumentRevision, TipoConfirmacion
from ..services import crud, people, read_confirmations
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('confirmacion_lectura', __name__, url_prefix='/confirmaciones')
FORM_FIELDS = tuple(field.name for field in read_confirmations.SPEC.fields)


def _revisions():
    # Only identifiers and numbers: never disclose draft text or attachments.
    return list(db.session.execute(db.select(DocumentRevision.id, DocumentRevision.document_id,
                                               DocumentRevision.numero)
                                   .order_by(DocumentRevision.document_id, DocumentRevision.numero.desc())))


def _form(found=None):
    form = ConfirmacionLecturaForm(obj=found)
    actor = current_actor()
    form.persona_id.choices = person_choices(
        people.choices(db.session, actor, include=found.persona_id if found else None), CHOOSE_PERSON)
    form.document_revision_id.choices = [('', '— Sin revisión —'),
        *((row.id, f'Documento n.º {row.document_id} · Revisión {row.numero} (id {row.id})')
          for row in _revisions())]
    if request.method == 'GET' and found is None:
        form.persona_id.data = request.args.get('persona_id', type=int)
        form.fecha_confirmacion.data = local_today()
    return form


@bp.get('/')
@login_required
@require_permission('read', 'read_confirmations')
def list_confirmaciones():
    actor = current_actor()
    tipo = request.args.get('tipo', '')
    tipo = tipo if tipo in TipoConfirmacion.__members__ else ''
    filters = dict(persona_id=request.args.get('persona_id', type=int), tipo=tipo or None,
                   document_revision_id=request.args.get('document_revision_id', type=int))
    page, per_page = crud.page_bounds(request.args.get('page', 1, type=int),
                                      request.args.get('per_page', crud.DEFAULT_PER_PAGE, type=int))
    found, total = read_confirmations.list_page(db.session, actor, **filters, page=page, per_page=per_page)
    return render_template('confirmaciones/list.html', confirmaciones=found, filters=filters,
                           people=people.list_(db.session, actor), tipos=TipoConfirmacion,
                           revisions=_revisions(), names=people.names(db.session, actor, (r.persona_id for r in found)),
                           page=page, per_page=per_page, total=total)


@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'read_confirmations')
def new_confirmacion():
    form = _form()
    if form.validate_on_submit() and (created := saved(lambda: read_confirmations.create(
            db.session, current_actor(), form_data(form, FORM_FIELDS), today=local_today()))):
        flash('Confirmación de lectura creada.', 'success')
        return redirect(url_for('confirmacion_lectura.show_confirmacion', confirmation_id=created.id))
    return render_template('confirmaciones/form.html', form=form, confirmation=None)


@bp.get('/<int:confirmation_id>')
@login_required
@require_permission('read', 'read_confirmations')
def show_confirmacion(confirmation_id):
    actor = current_actor()
    found = read_confirmations.get(db.session, actor, confirmation_id)
    return render_template('confirmaciones/show.html', confirmation=found,
                           names=people.names(db.session, actor, [found.persona_id]))


@bp.route('/<int:confirmation_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'read_confirmations')
def edit_confirmacion(confirmation_id):
    found = read_confirmations.get(db.session, current_actor(), confirmation_id)
    form = _form(found)
    if form.validate_on_submit() and saved(lambda: read_confirmations.update(
            db.session, current_actor(), confirmation_id, form_data(form, FORM_FIELDS), today=local_today())):
        flash('Confirmación de lectura actualizada.', 'success')
        return redirect(url_for('confirmacion_lectura.show_confirmacion', confirmation_id=confirmation_id))
    return render_template('confirmaciones/form.html', form=form, confirmation=found)


@bp.post('/<int:confirmation_id>/eliminar')
@login_required
@require_permission('delete', 'read_confirmations')
def delete_confirmacion(confirmation_id):
    if committed(lambda: read_confirmations.delete(db.session, current_actor(), confirmation_id)):
        flash('Confirmación de lectura eliminada.', 'success')
        return redirect(url_for('confirmacion_lectura.list_confirmaciones'))
    return redirect(url_for('confirmacion_lectura.show_confirmacion', confirmation_id=confirmation_id))
