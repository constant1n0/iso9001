"""Thin web adapters for the editable organizational knowledge register."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import CHOOSE_PERSON, KnowledgeForm, person_choices
from ..models import KnowledgeItem, KnowledgeType
from ..services import crud, knowledge, people
from ..services.errors import ValidationError
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('knowledge', __name__, url_prefix='/conocimientos')
RESOURCE = 'knowledge'
FORM_FIELDS = tuple(field.name for field in knowledge.SPEC.fields)


@bp.get('/')
@login_required
@require_permission('read', RESOURCE)
def list_items() -> ResponseReturnValue:
    """Page knowledge by type, overdue review and retirement, preserving filters."""
    page, per_page = crud.page_bounds(request.args.get('page', 1, type=int),
                                     request.args.get('per_page', 20, type=int))
    kind = request.args.get('kind', '')
    overdue = request.args.get('overdue', '')
    retired = request.args.get('retired', '0')
    if overdue not in ('', '0', '1') or retired not in ('', '0', '1'):
        raise ValidationError('Los filtros de revisión y retirada no son válidos.')
    today = local_today()
    items, total = knowledge.items.list_page(
        db.session, current_actor(), page=page, per_page=per_page, kind=kind or None,
        overdue={'': None, '0': False, '1': True}[overdue],
        retired={'': None, '0': False, '1': True}[retired], today=today)
    return render_template('knowledge/list.html', items=items, total=total,
                           page=page, per_page=per_page, kind=kind, overdue=overdue,
                           retired=retired, kinds=KnowledgeType, today=today,
                           is_overdue=knowledge.is_overdue)


def _form(item: KnowledgeItem | None = None) -> KnowledgeForm:
    """Offer active owners plus the item's existing owner, even if deactivated."""
    form = KnowledgeForm(obj=item)
    if item is not None and not form.is_submitted():
        # The select offers enum names; obj= would hand it the enum itself.
        form.kind.data = item.kind.name
    form.owner_id.choices = person_choices(people.choices(
        db.session, current_actor(), include=None if item is None else item.owner_id),
        empty=CHOOSE_PERSON)
    return form


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_item() -> ResponseReturnValue:
    """Create a knowledge item or lesson with audited attribution."""
    form = _form()
    if form.validate_on_submit() and (item := saved(lambda: knowledge.items.create(
            db.session, current_actor(), form_data(form, FORM_FIELDS)))):
        flash('Conocimiento registrado.', 'success')
        return redirect(url_for('knowledge.show_item', item_id=item.id))
    return render_template('knowledge/form.html', form=form, title='Nuevo conocimiento')


@bp.get('/<int:item_id>')
@login_required
@require_permission('read', RESOURCE)
def show_item(item_id: int) -> ResponseReturnValue:
    """Read an item, including retired knowledge and the substantive summary."""
    item = knowledge.items.get(db.session, current_actor(), item_id)
    owners = people.names(db.session, current_actor(), [item.owner_id])
    return render_template('knowledge/show.html', item=item, owners=owners,
                           overdue=knowledge.is_overdue(item, today=local_today()))


@bp.route('/<int:item_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_item(item_id: int) -> ResponseReturnValue:
    """Edit or retire an item; unchanged submissions do not add audit rows."""
    item = knowledge.items.get(db.session, current_actor(), item_id)
    form = _form(item)
    if form.validate_on_submit() and saved(lambda: knowledge.items.update(
            db.session, current_actor(), item.id, form_data(form, FORM_FIELDS))):
        flash('Conocimiento actualizado.', 'success')
        return redirect(url_for('knowledge.show_item', item_id=item.id))
    return render_template('knowledge/form.html', form=form, title='Editar conocimiento')


@bp.post('/<int:item_id>/eliminar')
@login_required
@require_permission('delete', RESOURCE)
def delete_item(item_id: int) -> ResponseReturnValue:
    """Allow administrators to delete only uncited records, through audited CRUD."""
    if committed(lambda: knowledge.items.delete(db.session, current_actor(), item_id)):
        return redirect(url_for('knowledge.list_items'))
    return redirect(url_for('knowledge.show_item', item_id=item_id))
