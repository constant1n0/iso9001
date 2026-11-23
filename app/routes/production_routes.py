"""Spanish PS1 screens; CSRF-protected lifecycle and strict submitted-field boundary."""
from typing import Any

from wtforms import SelectMultipleField
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required
from sqlalchemy import or_, select

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (BaseForm, DesignTransitionForm, ProductionOrderForm, person_choices,
                     ProductionControlForm, ProductionPreservationForm,
                     ProductionChangeForm, DesignChangeRejectionForm, PropertyForm,
                     PropertyTransitionForm, PropertyIncidentForm, PostDeliveryForm,
                     PostDeliveryCompletionForm, PostDeliveryCancellationForm)
from ..models import (DesignApprovalState, DesignOutput, DesignOutputVersion, EquipoMedicion,
                      Person, ProductionKind, ProductionState, NoConformidad,
                      DesignChange, DesignChangeState, CambioSGC, PropertyStatus,
                      Cliente, Proveedor, Reclamacion, PostDeliveryKind, PostDeliveryStatus)
from ..services import (clientes, customer_orders, design_io, equipment, people, process_operations,
                        production, production_records, production_changes, qms_scope,
                        production_property, production_post_delivery)
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .design_routes import _control_payload_refused
from .people_routes import form_data, saved

bp = Blueprint('production', __name__, url_prefix='/produccion')


def _post_delivery_choices() -> dict:
    return {'status': [(m.name, m.value) for m in PostDeliveryStatus],
        'kind': [(m.name, m.value) for m in PostDeliveryKind],
        'overdue': [('true', 'Vencidas'), ('false', 'No vencidas')],
        **{key: [(str(r.id), r.nombre) for r in db.session.scalars(select(model).order_by(model.id))]
           for key, model in (('customer_id', Cliente), ('responsible_id', Person))}}


@bp.route('/posventa/')
@login_required
@require_permission('read', 'production')
def list_post_delivery() -> ResponseReturnValue:
    """Ignore unknown filter values; overdue is computed on the local day."""
    choices = _post_delivery_choices()
    selected = {k: v for k in choices if (v := request.args.get(k, '')) in dict(choices[k])}
    filters = {k: int(v) if k.endswith('_id') else v for k, v in selected.items()}
    if 'overdue' in filters:
        filters['overdue'] = filters['overdue'] == 'true'
    today = local_today()
    return render_template('production/post_delivery_list.html', choices=choices, selected=selected,
        items=production_post_delivery.activities.list_(db.session, current_actor(), today=today, **filters),
        today=today, is_overdue=production_post_delivery.is_overdue)


@bp.route('/posventa/<int:activity_id>')
@login_required
@require_permission('read', 'production')
def show_post_delivery(activity_id: int) -> ResponseReturnValue:
    """Read obligations and immutable dated outcomes, without release authority."""
    return render_template('production/post_delivery_detail.html',
        item=production_post_delivery.activities.get(db.session, current_actor(), activity_id),
        complete_form=PostDeliveryCompletionForm(), cancel_form=PostDeliveryCancellationForm())


@bp.route('/posventa/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'production')
def new_post_delivery() -> ResponseReturnValue:
    """Create a pending obligation."""
    return _edit_post_delivery(None)


@bp.route('/posventa/<int:activity_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'production')
def edit_post_delivery(activity_id: int) -> ResponseReturnValue:
    """Edit pending obligations only."""
    return _edit_post_delivery(activity_id)


def _post_delivery_refused(form: Any) -> bool:
    refused = _refused(form)
    if request.method == 'POST' and (missing := {f.name for f in form if f.name != 'csrf_token'} - set(request.form)):
        flash(f'Faltan campos del formulario: {", ".join(sorted(missing))}.', 'danger')
        refused = True
    picked = request.form.getlist('order_ids') if request.method == 'POST' else []
    if '' in picked and any(value != '' for value in picked):
        flash('Elige «Sin órdenes» o una o varias órdenes, no ambas cosas.', 'danger')
        refused = True
    return refused


def _edit_post_delivery(activity_id: int | None) -> ResponseReturnValue:
    who = current_actor()
    row = None if activity_id is None else production_post_delivery.activities.get(db.session, who, activity_id)
    if row is not None and row.status is not PostDeliveryStatus.pending:
        flash(production_post_delivery.READ_ONLY, 'danger')
        return redirect(url_for('production.show_post_delivery', activity_id=activity_id))
    form = PostDeliveryForm(obj=row)
    form.customer_id.choices = [('', '— Sin cliente —'), *_post_delivery_choices()['customer_id']]
    form.responsible_id.choices = person_choices(people.choices(db.session, who, include=getattr(row, 'responsible_id', None)))
    for key, model in (('complaint_id', Reclamacion), ('nonconformity_id', NoConformidad)):
        form[key].choices = [('', '— Ninguna —'), *((r.id, f'#{r.id} · {r.descripcion[:80]}')
            for r in db.session.scalars(select(model).order_by(model.id))) ]
    form.order_ids.choices = [('', '— Sin órdenes —'), *((o.id, o.code) for o in production.orders.list_(db.session, who)
        if o.state is not ProductionState.cancelled or o.id in getattr(row, 'order_ids', []))]
    if request.method == 'GET':
        form.order_ids.data = getattr(row, 'order_ids', []) or [None]
        form.kind.data = row.kind.name if row else 'warranty'
        form.due_date.data = row.due_date if row else local_today()
    refused = _post_delivery_refused(form)
    if not refused and form.validate_on_submit():
        data = form_data(form, production_post_delivery.SPEC.writable)
        data['order_ids'] = [value for value in data['order_ids'] if value is not None]
        result = saved(lambda: production_post_delivery.activities.create(db.session, who, data, today=local_today())
            if row is None else production_post_delivery.activities.update(db.session, who, row.id, data, today=local_today()))
        if result:
            return redirect(url_for('production.show_post_delivery', activity_id=result.id))
    return render_template('production/post_delivery_form.html', form=form,
        title='Nueva actividad de posventa' if row is None else 'Editar actividad de posventa'), 400 if refused else 200


@bp.route('/posventa/<int:activity_id>/realizar', methods=['POST'])
@login_required
@require_permission('update', 'production')
def complete_post_delivery(activity_id: int) -> ResponseReturnValue:
    """Web-only completion with CSRF and dated evidence."""
    return _post_delivery_action(activity_id, 'completed', PostDeliveryCompletionForm())


@bp.route('/posventa/<int:activity_id>/anular', methods=['POST'])
@login_required
@require_permission('update', 'production')
def cancel_post_delivery(activity_id: int) -> ResponseReturnValue:
    """Web-only dated cancellation, never a product release."""
    return _post_delivery_action(activity_id, 'cancelled', PostDeliveryCancellationForm())


@bp.route('/posventa/<int:activity_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'production')
def delete_post_delivery(activity_id: int) -> ResponseReturnValue:
    """Only web administrators delete pending activities without orders."""
    return _post_delivery_action(activity_id, None, BaseForm())


def _post_delivery_action(activity_id: int, target: str | None, form: Any) -> ResponseReturnValue:
    refused = _post_delivery_refused(form)
    if not refused and form.validate_on_submit():
        if target is None:
            if committed(lambda: production_post_delivery.activities.delete(db.session, current_actor(), activity_id)):
                return redirect(url_for('production.list_post_delivery'))
        else:
            committed(lambda: production_post_delivery.transition(db.session, current_actor(), activity_id,
                target, today=local_today(), **form_data(form, {f.name for f in form if f.name != 'csrf_token'})))
    return redirect(url_for('production.show_post_delivery', activity_id=activity_id)), 400 if refused else 302


def _property_choices() -> dict:
    return {'owner_type': [('customer', 'Cliente'), ('provider', 'Proveedor')],
        'status': [(m.name, m.value) for m in PropertyStatus],
        **{key: [(str(r.id), r.nombre) for r in db.session.scalars(select(model).order_by(model.id))]
           for key, model in (('customer_id', Cliente), ('provider_id', Proveedor))}}


@bp.route('/propiedad/')
@login_required
@require_permission('read', 'production')
def list_property() -> ResponseReturnValue:
    """Ignore unknown selector values, including nonexistent owner ids."""
    choices = _property_choices()
    selected = {k: v for k in choices if (v := request.args.get(k, '')) in dict(choices[k])}
    filters = {k: int(v) if k.endswith('_id') else v for k, v in selected.items()}
    return render_template('production/property_list.html', choices=choices, selected=selected,
        items=production_property.items.list_(db.session, current_actor(), **filters))


@bp.route('/propiedad/<int:item_id>')
@login_required
@require_permission('read', 'production')
def show_property(item_id: int) -> ResponseReturnValue:
    """Retain the complete incident history even for returned or disposed items."""
    return render_template('production/property_detail.html',
        item=production_property.items.get(db.session, current_actor(), item_id),
        incidents=production_property.incidents.list_(db.session, current_actor(), item_id=item_id),
        transition_form=PropertyTransitionForm(), delete_form=BaseForm())


@bp.route('/propiedad/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'production')
def new_property() -> ResponseReturnValue:
    """Receive customer/provider property through the shared service."""
    return _edit_property(None)


@bp.route('/propiedad/<int:item_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'production')
def edit_property(item_id: int) -> ResponseReturnValue:
    """Edit custody only; terminal fields are never accepted here."""
    return _edit_property(item_id)


def _property_submit(form: Any, operation: Any, title: str) -> ResponseReturnValue:
    refused = _refused(form)
    if request.method == 'POST' and (missing := {f.name for f in form if f.name != 'csrf_token'} - set(request.form)):
        flash(f'Faltan campos del formulario: {", ".join(sorted(missing))}.', 'danger')
        refused = True
    picked = request.form.getlist('order_ids') if request.method == 'POST' else []
    if '' in picked and any(value != '' for value in picked):
        # «Sin órdenes» plus an order contradict each other: refuse, never pick one.
        flash('Elige «Sin órdenes» o una o varias órdenes, no ambas cosas.', 'danger')
        refused = True
    if not refused and form.validate_on_submit() and (result := saved(operation)):
        return redirect(url_for('production.show_property', item_id=getattr(result, 'item_id', result.id)))
    return render_template('production/property_form.html', form=form, title=title), 400 if refused else 200


def _edit_property(item_id: int | None) -> ResponseReturnValue:
    who = current_actor()
    row = None if item_id is None else production_property.items.get(db.session, who, item_id)
    if row is not None and row.status is not PropertyStatus.custody:
        flash(production_property.READ_ONLY, 'danger')
        return redirect(url_for('production.show_property', item_id=item_id))
    form = PropertyForm(obj=row)
    choices = _property_choices()
    for key in ('customer_id', 'provider_id'):
        form[key].choices = [('', '— Ninguno —'), *choices[key]]
    form.responsible_id.choices = person_choices(people.choices(db.session, who, include=getattr(row, 'responsible_id', None)))
    form.order_ids.choices = [('', '— Sin órdenes —'), *((o.id, o.code) for o in production.orders.list_(db.session, who)
        if o.state is not ProductionState.cancelled or o.id in getattr(row, 'order_ids', []))]
    if request.method == 'GET':
        form.order_ids.data = getattr(row, 'order_ids', []) or [None]
        form.received_date.data = row.received_date if row else local_today()
    def operation() -> Any:
        data = form_data(form, production_property.SPEC.writable)
        data['order_ids'] = [value for value in data['order_ids'] if value is not None]
        return (production_property.items.create(db.session, who, data, today=local_today()) if row is None
            else production_property.items.update(db.session, who, row.id, data, today=local_today()))
    return _property_submit(form, operation, 'Recibir propiedad' if row is None else 'Editar custodia')


@bp.route('/propiedad/<int:item_id>/incidencias/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'production')
def new_property_incident(item_id: int) -> ResponseReturnValue:
    """Append a notified incident, also after return/disposal; no edit/delete routes."""
    production_property.items.get(db.session, current_actor(), item_id)
    form = PropertyIncidentForm()
    for key, model, label in (('complaint_id', Reclamacion, 'descripcion'),
                              ('nonconformity_id', NoConformidad, 'descripcion')):
        form[key].choices = [('', '— Ninguna —'), *((r.id, f'#{r.id} · {getattr(r, label)[:80]}')
            for r in db.session.scalars(select(model).order_by(model.id))) ]
    if request.method == 'GET':
        form.date.data = form.notification_date.data = local_today()
        form.kind.data, form.owner_notified.data = 'damage', 'true'
    def operation() -> Any:
        data = form_data(form, production_property.INCIDENT_SPEC.writable - {'item_id'})
        data['owner_notified'] = form.owner_notified.data == 'true'
        return production_property.incidents.create(db.session, current_actor(),
            data | {'item_id': item_id}, today=local_today())
    return _property_submit(form, operation, 'Registrar incidencia notificada al dueño')


@bp.route('/propiedad/<int:item_id>/transicion', methods=['POST'])
@login_required
@require_permission('update', 'production')
def transition_property(item_id: int) -> ResponseReturnValue:
    """Return/dispose via explicit CSRF-protected POST only."""
    form = PropertyTransitionForm()
    refused = _refused(form)
    if not refused and form.validate_on_submit():
        committed(lambda: production_property.transition(db.session, current_actor(), item_id,
            form.target.data, terminal_date=form.terminal_date.data,
            terminal_note=form.terminal_note.data, today=local_today()))
    return redirect(url_for('production.show_property', item_id=item_id)), 400 if refused else 302


@bp.route('/propiedad/<int:item_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'production')
def delete_property(item_id: int) -> ResponseReturnValue:
    """Only administrators delete unlinked custody items without incidents."""
    form = BaseForm()
    refused = _refused(form)
    if not refused and form.validate_on_submit() and committed(
            lambda: production_property.items.delete(db.session, current_actor(), item_id)):
        return redirect(url_for('production.list_property'))
    return redirect(url_for('production.show_property', item_id=item_id)), 400 if refused else 302


def _applicability() -> dict | None:
    scope = qms_scope.current(db.session, current_actor())
    values = None if scope is None else qms_scope.applicability_of(db.session, scope.id)
    return None if values is None else {code: values[code] for code in ('8.5.1', '8.5.2')}


def _choices() -> dict:
    who = current_actor()
    return {'state': [(m.name, m.value) for m in ProductionState],
            'kind': [(m.name, m.value) for m in ProductionKind],
            'process_id': [(str(p.id_proceso), p.proceso) for p in process_operations.list_(db.session, who)],
            'customer_id': [(str(c.id), c.nombre) for c in clientes.list_(db.session, who)]}


@bp.route('/')
@login_required
@require_permission('read', 'production')
def list_orders() -> ResponseReturnValue:
    """Ignore unknown selector filters, as the other register screens do."""
    choices = _choices()
    selected = {key: value for key in choices if (value := request.args.get(key, ''))
                in {v for v, _ in choices[key]}}
    filters = {key: int(value) if key.endswith('_id') else value for key, value in selected.items()}
    selected['lot'] = request.args.get('lot', '')
    return render_template('production/list.html', choices=choices, selected=selected,
        orders=production.orders.list_(db.session, current_actor(), **filters, lot=selected['lot']),
        applicability=_applicability())


@bp.route('/<int:order_id>')
@login_required
@require_permission('read', 'production')
def show_order(order_id: int) -> ResponseReturnValue:
    """Read exact conditions with current suitability and neutral restricted citations."""
    who = current_actor()
    row = production.orders.get(db.session, who, order_id)
    labels = dict(design_io.revision_choices(db.session, who))
    return render_template('production/detail.html', order=row, applicability=_applicability(),
        revision_labels={rid: labels.get(rid, 'Revisión vinculada') for rid in row.revision_ids},
        suitability={item.id: equipment.status(db.session, item, today=local_today()) for item in row.equipment},
        targets=production.TRANSITIONS.get(row.state, ()), transition_form=DesignTransitionForm(),
        editable=row.state in production.TRANSITIONS,
        changes=production_changes.changes.list_(db.session, who, order_id=row.id),
        record_sections=[dict(slug=slug, title=title, rows=register.list_(db.session, who, order_id=row.id),
                              allowed=production_records.allowed(register.SPEC, row.state))
                         for slug, title, register in RECORD_SECTIONS])


RECORD_SECTIONS = (
    ('controles', 'Registros de ejecución y control', production_records.controls),
    ('preservacion', 'Preservación', production_records.preservation),
)


@bp.route('/<int:order_id>/controles/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'production')
def new_control(order_id: int) -> ResponseReturnValue:
    """Append immutable execution evidence; no edit/delete endpoints exist."""
    return _new_record(order_id, control=True)


@bp.route('/<int:order_id>/preservacion/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'production')
def new_preservation(order_id: int) -> ResponseReturnValue:
    """Append preservation, including storage after work finishes."""
    return _new_record(order_id, control=False)


def _new_record(order_id: int, *, control: bool) -> ResponseReturnValue:
    """Populate explicit GET defaults and reuse the strict PS1 payload boundary."""
    who = current_actor()
    order = production.orders.get(db.session, who, order_id)
    register = production_records.controls if control else production_records.preservation
    form = (ProductionControlForm if control else ProductionPreservationForm)()
    if control:
        del form['aspect']
        form.equipment_id.choices = [('', '— Sin equipo —'), *((item.id, f'{item.codigo} · {item.nombre}')
            for item in db.session.scalars(select(EquipoMedicion).order_by(EquipoMedicion.codigo)))]
    form.responsible_id.choices = person_choices(people.choices(db.session, who))
    form.nonconformity_id.choices = [('', '— Sin no conformidad —'), *((nc.id, f'NC #{nc.id} · {nc.descripcion[:80]}')
        for nc in db.session.scalars(select(NoConformidad).order_by(NoConformidad.id)))]
    if request.method == 'GET':
        form['kind' if control else 'aspect'].data = 'inspection' if control else 'identification'
        form.result.data, form.date.data = 'conforming', local_today()
    title = 'Registrar ejecución y control' if control else 'Registrar preservación'
    if _refused(form):
        return render_template('production/record_form.html', form=form, order=order, title=title), 400
    if request.method == 'POST' and (missing := {field.name for field in form
            if field.name != 'csrf_token'} - set(request.form)):
        flash(f'Faltan campos del formulario: {", ".join(sorted(missing))}.', 'danger')
        return render_template('production/record_form.html', form=form, order=order, title=title), 400
    if form.validate_on_submit() and saved(lambda: register.create(db.session, who,
            {'order_id': order.id} | form_data(form, tuple(f.name for f in register.SPEC.fields
                                                          if f.name != 'order_id')),
            today=local_today())):
        return redirect(url_for('production.show_order', order_id=order.id))
    return render_template('production/record_form.html', form=form, order=order, title=title)


def _form(row: Any = None) -> ProductionOrderForm:
    who = current_actor()
    form = ProductionOrderForm(obj=row)
    choices = _choices()
    form.process_id.choices = [('', '— Elige un proceso —'), *choices['process_id']]
    form.customer_id.choices = [('', '— Sin cliente —'), *choices['customer_id']]
    form.owner_id.choices = person_choices(people.choices(db.session, who, include=getattr(row, 'owner_id', None)))
    form.customer_order_id.choices = [('', '— Sin pedido —'), *((o.id, o.code)
        for o in customer_orders.orders.list_(db.session, who)
        if o.status.name == 'accepted' or o.id == getattr(row, 'customer_order_id', None))]
    current = getattr(row, 'design_output_id', None)
    outputs = db.session.scalars(select(DesignOutput).join(DesignOutputVersion,
        DesignOutputVersion.output_id == DesignOutput.id).where(or_(
        DesignOutputVersion.state == DesignApprovalState.approved, DesignOutput.id == current))
        .order_by(DesignOutput.id))
    form.design_output_id.choices = [('', '— Sin diseño —'), *((o.id,
        f'#{o.lineage_id} · Versión {o.version} · {o.text[:80]}') for o in outputs)]
    revisions = dict(design_io.revision_choices(db.session, who))
    for rid in getattr(row, 'revision_ids', []):
        revisions.update(design_io.revision_choices(db.session, who, rid))
    form.revision_ids.choices = [(key, label) for key, label in revisions.items() if key != '']
    form.equipment_ids.choices = [(item.id, f'{item.codigo} · {item.nombre}') for item in
        db.session.scalars(select(EquipoMedicion).order_by(EquipoMedicion.id))
        if item.id in getattr(row, 'equipment_ids', []) or equipment.status(db.session, item, today=local_today()) == 'apto']
    form.person_ids.choices = [(p.id, p.nombre) for p in db.session.scalars(select(Person).where(
        or_(Person.activo.is_(True), Person.id.in_(getattr(row, 'person_ids', [])))).order_by(Person.id))]
    if request.method == 'GET':
        form.kind.data = row.kind.name if row else 'production'
        for key in production.LINKS:
            form[key].data = getattr(row, key, [])
    return form


FORM_MIMETYPES = ('application/x-www-form-urlencoded', 'multipart/form-data')


def _refused(form: Any) -> bool:
    if request.method == 'POST':
        if request.mimetype not in FORM_MIMETYPES and request.get_data(cache=True):
            # A JSON or other body would be silently ignored by form handling: refuse it.
            flash('Envía los datos desde el formulario.', 'danger')
            return True
        unknown = (set(request.form) | set(request.files)) - {field.name for field in form}
        if unknown:
            flash(f'Campos no permitidos: {", ".join(sorted(unknown))}.', 'danger')
            return True
    return _control_payload_refused(form, 0)


def _repeated(form: Any) -> bool:
    """Whether the post repeats a single field or an option of a multi-select."""
    for name in request.form:
        values, field = request.form.getlist(name), form._fields.get(name)
        multiple = isinstance(field, SelectMultipleField)
        if (len(values) > 1 and not multiple) or len(values) != len(set(values)):
            return True
    return False


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'production')
def new_order() -> ResponseReturnValue:
    """Create an order through the same service as MCP."""
    return _edit(None)


@bp.route('/<int:order_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'production')
def edit_order(order_id: int) -> ResponseReturnValue:
    """Edit an order through the same service as MCP."""
    return _edit(order_id)


def _edit(order_id: int | None) -> ResponseReturnValue:
    """Create/edit via the same service as MCP; every selector round-trips exactly."""
    who = current_actor()
    row = None if order_id is None else production.orders.get(db.session, who, order_id)
    if row is not None and row.state not in production.TRANSITIONS:
        flash(production.READ_ONLY, 'danger')
        return redirect(url_for('production.show_order', order_id=order_id))
    form = _form(row)
    title = 'Nueva orden' if row is None else 'Editar orden'
    if _refused(form):
        if request.method == 'POST' and _repeated(form):
            flash('Envía cada campo una sola vez y no repitas las opciones seleccionadas.', 'danger')
        return render_template('production/form.html', form=form, title=title), 400
    if form.validate_on_submit():
        data = form_data(form, tuple(f.name for f in production.SPEC.fields) + tuple(production.LINKS))
        result = saved(lambda: production.orders.create(db.session, who, data) if row is None
                       else production.orders.update(db.session, who, order_id, data))
        if result:
            return redirect(url_for('production.show_order', order_id=result.id))
    return render_template('production/form.html', form=form, title=title)


@bp.route('/<int:order_id>/transicion', methods=['POST'])
@login_required
@require_permission('update', 'production')
def transition_order(order_id: int) -> ResponseReturnValue:
    """Start, finish or cancel an order on the web."""
    return _action(order_id, deleting=False)


@bp.route('/<int:order_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'production')
def delete_order(order_id: int) -> ResponseReturnValue:
    """Delete a planned order on the web."""
    return _action(order_id, deleting=True)


def _action(order_id: int, *, deleting: bool) -> ResponseReturnValue:
    """Explicit web POST actions; service rechecks role and state under the row lock."""
    form = BaseForm() if deleting else DesignTransitionForm()
    if _refused(form):
        return redirect(url_for('production.show_order', order_id=order_id)), 400
    operation = (lambda: production.orders.delete(db.session, current_actor(), order_id)) if deleting else (
        lambda: production.transition(db.session, current_actor(), order_id, form.target.data,
                                      cancellation_reason=form.cancellation_reason.data))
    if form.validate_on_submit() and committed(operation):
        return redirect(url_for('production.list_orders' if deleting else 'production.show_order',
                                **({} if deleting else {'order_id': order_id})))
    return redirect(url_for('production.show_order', order_id=order_id))


@bp.route('/cambios/')
@login_required
@require_permission('read', 'production')
def list_changes() -> ResponseReturnValue:
    """Filter retained change history by state and affected order."""
    selected = request.args.get('state', '')
    state = selected if selected in DesignChangeState.__members__ else None
    order_id = request.args.get('order_id', type=int)
    return render_template('production/change_list.html', states=DesignChangeState,
        selected=state, order_id=order_id,
        orders=production.orders.list_(db.session, current_actor()),
        changes=production_changes.changes.list_(db.session, current_actor(),
                                                state=state, order_id=order_id))


@bp.route('/cambios/<int:change_id>')
@login_required
@require_permission('read', 'production')
def show_change(change_id: int) -> ResponseReturnValue:
    """Show review, citations and the independent decision."""
    row = production_changes.changes.get(db.session, current_actor(), change_id)
    return render_template('production/change_detail.html', change=row,
        may_decide=production_changes.may_decide(db.session, current_actor(), row),
        action_form=BaseForm(), rejection_form=DesignChangeRejectionForm())


@bp.route('/cambios/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'production')
def new_change() -> ResponseReturnValue:
    """Propose through the shared audited service."""
    return _edit_change(None)


@bp.route('/cambios/<int:change_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'production')
def edit_change(change_id: int) -> ResponseReturnValue:
    """Edit only proposals, refusing even decided no-op submissions."""
    return _edit_change(change_id)


def _edit_change(change_id: int | None) -> ResponseReturnValue:
    who = current_actor()
    row = None if change_id is None else production_changes.changes.get(db.session, who, change_id)
    if row is not None and row.state is not DesignChangeState.proposed:
        flash(production_changes.design_changes.IMMUTABLE, 'danger')
        return redirect(url_for('production.show_change', change_id=change_id))
    form = ProductionChangeForm(obj=row)
    form.order_ids.choices = [(o.id, o.code) for o in production.orders.list_(db.session, who)
        if o.state is not ProductionState.cancelled or o.id in getattr(row, 'order_ids', [])]
    form.design_change_id.choices = [('', '— Sin cambio del diseño —'), *((c.id, f'#{c.id} · {c.reason}')
        for c in db.session.scalars(select(DesignChange).where(or_(
            DesignChange.state == DesignChangeState.authorized,
            DesignChange.id == getattr(row, 'design_change_id', None))).order_by(DesignChange.id)))]
    form.qms_change_id.choices = [('', '— Sin cambio del SGC —'), *((c.id, f'#{c.id} · {c.titulo}')
        for c in db.session.scalars(select(CambioSGC).order_by(CambioSGC.id)))]
    if request.method == 'GET':
        form.order_ids.data = getattr(row, 'order_ids', [])
    title = 'Proponer cambio de producción' if row is None else 'Editar cambio de producción'
    refused = _refused(form)
    if request.method == 'POST' and (missing := {f.name for f in form
            if f.name != 'csrf_token'} - set(request.form)):
        flash(f'Faltan campos del formulario: {", ".join(sorted(missing))}.', 'danger')
        refused = True
    if not refused and form.validate_on_submit():
        data = form_data(form, production_changes.SPEC.writable)
        result = saved(lambda: production_changes.changes.create(db.session, who, data) if row is None
            else production_changes.changes.update(db.session, who, row.id, data))
        if result:
            return redirect(url_for('production.show_change', change_id=result.id))
    return render_template('production/change_form.html', form=form, title=title), 400 if refused else 200


@bp.route('/cambios/<int:change_id>/autorizar', methods=['POST'])
@login_required
@require_permission('update', 'production')
def authorize_change(change_id: int) -> ResponseReturnValue:
    """Web-only independent administrator authorization."""
    return _decide_change(change_id, rejecting=False)


@bp.route('/cambios/<int:change_id>/rechazar', methods=['POST'])
@login_required
@require_permission('update', 'production')
def reject_change(change_id: int) -> ResponseReturnValue:
    """Retain the proposal and explicit rejection reason."""
    return _decide_change(change_id, rejecting=True)


def _decide_change(change_id: int, *, rejecting: bool) -> ResponseReturnValue:
    form = DesignChangeRejectionForm() if rejecting else BaseForm()
    refused = _refused(form)
    if not refused and form.validate_on_submit():
        committed(lambda: production_changes.reject(db.session, current_actor(), change_id,
            reason=form.reason.data) if rejecting else production_changes.authorize(
                db.session, current_actor(), change_id, today=local_today()))
    return redirect(url_for('production.show_change', change_id=change_id)), 400 if refused else 302
