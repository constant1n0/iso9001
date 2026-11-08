"""Thin web adapters for the editable organizational knowledge register."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (
    CHOOSE_PERSON, KnowledgeForm, person_choices, process_choices,
    requirement_choices, training_choices,
)
from ..models import KnowledgeItem, KnowledgeType
from ..services import (
    audit_findings, crud, documents, improvements, knowledge, management_reviews,
    nonconformities, people, process_operations, training,
)
from ..services.errors import ValidationError
from ..utils.permissions import can, require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, requirement_labels, saved

bp = Blueprint('knowledge', __name__, url_prefix='/conocimientos')
RESOURCE = 'knowledge'
FORM_FIELDS = (*[field.name for field in knowledge.SPEC.fields], *knowledge.LINKS)
ORIGINS = {
    'no_conformidad_id': ('nonconformities', nonconformities.list_, 'No conformidad'),
    'hallazgo_id': ('audits', audit_findings.list_, 'Hallazgo de auditoría'),
    'mejora_id': ('improvements', improvements.list_, 'Mejora'),
    'accion_revision_id': ('management_review', management_reviews.actions.list_,
                           'Acción de revisión por la dirección'),
}


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
    actor = current_actor()
    form.process_ids.choices = process_choices(process_operations.list_(db.session, actor))[1:]
    existing_documents = set(getattr(item, 'document_ids', []))
    form.document_ids.choices = [
        (doc.id, f'{doc.code} · {doc.title}' + (' (de baja)' if doc.withdrawn_at else ''))
        for doc in documents.list_(db.session, actor)
        if doc.withdrawn_at is None or doc.id in existing_documents]
    existing_experts = set(getattr(item, 'expert_ids', []))
    form.expert_ids.choices = person_choices([
        person for person in people.list_(db.session, actor)
        if person.activo or person.id in existing_experts])[1:]
    form.training_ids.choices = training_choices(training.list_(db.session, actor))[1:]
    form.requirement_ids.choices = requirement_choices(requirement_labels(actor))[1:]
    for key, (resource, listing, label) in ORIGINS.items():
        records = listing(db.session, actor) if can('read', resource) else []
        choices = [('', '— Sin registro de origen —')]
        for record in records:
            record_id = record.id_mejora if key == 'mejora_id' else record.id
            choices.append((record_id, f'{label} n.º {record_id} · {record.descripcion[:90]}'))
        getattr(form, key).choices = choices
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
    visible_documents = {doc.id for doc in documents.list_(db.session, current_actor())}
    return render_template('knowledge/show.html', item=item, owners=owners,
                           visible_documents=visible_documents, origin=_origin(item),
                           overdue=knowledge.is_overdue(item, today=local_today()))


def _origin(item: KnowledgeItem) -> dict | None:
    """Link to an origin without exposing data protected by another resource."""
    for key, (resource, _, label) in ORIGINS.items():
        record_id = getattr(item, key)
        if record_id is None:
            continue
        if not can('read', resource):
            return {'label': 'Registro vinculado', 'url': None}
        if key == 'no_conformidad_id':
            url = url_for('no_conformidad.ver_no_conformidad', id=record_id)
        elif key == 'mejora_id':
            url = url_for('mejora.ver_mejora', id=record_id)
        elif key == 'hallazgo_id':
            record = audit_findings.get(db.session, current_actor(), record_id)
            url = url_for('auditoria.ver_auditoria', id=record.auditoria_id,
                          _anchor=f'hallazgo-{record_id}')
        else:
            record = management_reviews.actions.get(db.session, current_actor(), record_id)
            url = url_for('revision_direccion.show_review', review_id=record.revision_id,
                          _anchor=f'accion-{record_id}')
        return {'label': f'{label} n.º {record_id}', 'url': url}
    return None


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
