"""Thin Spanish web adapters for the communication plan register (CM1)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import CHOOSE_PERSON, CommunicationForm, person_choices
from ..models import CommunicationChannel, CommunicationPlan, CommunicationScope, EstadoProveedor
from ..services import clientes, communication, crud, people, providers, stakeholders
from ..services.errors import ValidationError
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('communication', __name__, url_prefix='/comunicaciones')
RESOURCE = 'communication'
FORM_FIELDS = (*[field.name for field in communication.SPEC.fields], *communication.LINKS)


@bp.get('/')
@login_required
@require_permission('read', RESOURCE)
def list_items() -> ResponseReturnValue:
    """Page plans by scope, channel, overdue date and retirement."""
    page, per_page = crud.page_bounds(request.args.get('page', 1, type=int),
                                     request.args.get('per_page', 20, type=int))
    scope = request.args.get('scope', '')
    channel = request.args.get('channel', '')
    overdue = request.args.get('overdue', '')
    retired = request.args.get('retired', '0')
    if overdue not in ('', '0', '1') or retired not in ('', '0', '1'):
        raise ValidationError('Los filtros de vencimiento y retirada no son válidos.')
    today = local_today()
    items, total = communication.items.list_page(
        db.session, current_actor(), page=page, per_page=per_page,
        scope=scope or None, channel=channel or None,
        overdue={'': None, '0': False, '1': True}[overdue],
        retired={'': None, '0': False, '1': True}[retired], today=today)
    return render_template('communication/list.html', items=items, total=total,
                           page=page, per_page=per_page, scope=scope, channel=channel,
                           overdue=overdue, retired=retired, scopes=CommunicationScope,
                           channels=CommunicationChannel, today=today,
                           is_overdue=communication.is_overdue)


def _form(item: CommunicationPlan | None = None) -> CommunicationForm:
    """Offer current targets plus existing links and preselect every stored value."""
    form = CommunicationForm(obj=item)
    actor = current_actor()
    if item is not None and not form.is_submitted():
        form.scope.data = item.scope.name
        form.channel.data = item.channel.name
        for key in communication.LINKS:
            getattr(form, key).data = getattr(item, key)
    form.owner_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(item, 'owner_id', None)), empty=CHOOSE_PERSON)
    form.stakeholder_ids.choices = [(p.id_interesado, p.nombre)
                                    for p in stakeholders.list_(db.session, actor)]
    form.customer_ids.choices = [(p.id, p.nombre) for p in clientes.list_(db.session, actor)]
    existing_providers = set(getattr(item, 'provider_ids', []))
    form.provider_ids.choices = [
        (p.id, p.nombre + (' (bloqueado)' if p.estado is EstadoProveedor.bloqueado else ''))
        for p in providers.providers.list_(db.session, actor)
        if p.estado is not EstadoProveedor.bloqueado or p.id in existing_providers]
    existing_people = set(getattr(item, 'person_ids', []))
    form.person_ids.choices = person_choices([
        p for p in people.list_(db.session, actor) if p.activo or p.id in existing_people])[1:]
    return form


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_item() -> ResponseReturnValue:
    """Create a plan, without sending or recording a communication occurrence."""
    form = _form()
    if form.validate_on_submit() and (item := saved(lambda: communication.items.create(
            db.session, current_actor(), form_data(form, FORM_FIELDS)))):
        flash('Comunicación planificada registrada.', 'success')
        return redirect(url_for('communication.show_item', item_id=item.id))
    return render_template('communication/form.html', form=form, title='Nueva comunicación planificada')


@bp.get('/<int:item_id>')
@login_required
@require_permission('read', RESOURCE)
def show_item(item_id: int) -> ResponseReturnValue:
    """Read every field and audience, including retired plans."""
    item = communication.items.get(db.session, current_actor(), item_id)
    owners = people.names(db.session, current_actor(), [item.owner_id])
    return render_template('communication/show.html', item=item, owners=owners,
                           overdue=communication.is_overdue(item, today=local_today()))


@bp.route('/<int:item_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_item(item_id: int) -> ResponseReturnValue:
    """Edit or retire a plan; a no-op submission writes no audit row."""
    item = communication.items.get(db.session, current_actor(), item_id)
    form = _form(item)
    if form.validate_on_submit() and saved(lambda: communication.items.update(
            db.session, current_actor(), item.id, form_data(form, FORM_FIELDS))):
        flash('Comunicación planificada actualizada.', 'success')
        return redirect(url_for('communication.show_item', item_id=item.id))
    return render_template('communication/form.html', form=form, title='Editar comunicación planificada')


@bp.post('/<int:item_id>/eliminar')
@login_required
@require_permission('delete', RESOURCE)
def delete_item(item_id: int) -> ResponseReturnValue:
    """Allow administrators to delete only uncited entries through audited services."""
    if committed(lambda: communication.items.delete(db.session, current_actor(), item_id)):
        return redirect(url_for('communication.list_items'))
    return redirect(url_for('communication.show_item', item_id=item_id))
