"""CSRF-protected design projects, versions, changes and immutable evidence."""
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (BaseForm, CHOOSE_PERSON, DesignChangeForm,
                     DesignChangeRejectionForm, DesignInputForm, DesignOutputForm,
                     DesignProjectForm, DesignStageForm, DesignTransitionForm,
                     DesignPlannedControlForm, DesignControlRecordForm,
                     DesignControlWithdrawalForm, person_choices)
from ..models import (DesignInputType, DesignOutputType, DesignProject,
                      DesignStage, DesignState, Document)
from ..services import (clientes, design, design_changes, design_controls, design_io, document_revisions,
                        people, process_operations, qms_scope)
from ..services.actor import Actor
from ..services.errors import NotFound
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('design', __name__, url_prefix='/diseno')
RESOURCE = 'design'
PROJECT_FIELDS = tuple(field.name for field in design.PROJECT_SPEC.fields) + ('process_ids',)
STAGE_FIELDS = tuple(field.name for field in design.STAGE_SPEC.fields if field.name != 'project_id')


def _page(project_id: int) -> ResponseReturnValue:
    return redirect(url_for('design.show_project', project_id=project_id))


def _applicability(actor: Actor) -> dict[str, Any] | None:
    """Read the approved scope only; this module never changes applicability."""
    current = qms_scope.current(db.session, actor)
    return None if current is None else qms_scope.applicability_of(db.session, current.id)['8.3']


@bp.route('/')
@login_required
@require_permission('read', RESOURCE)
def list_projects() -> ResponseReturnValue:
    """List projects with state, owner and customer filters."""
    actor = current_actor()
    choices = {
        'state': [(m.name, m.value) for m in DesignState],
        'owner_id': [(str(p.id), p.nombre) for p in people.list_(db.session, actor)],
        'customer_id': [(str(c.id), c.nombre) for c in clientes.list_(db.session, actor)],
    }
    filters = {key: value for key in choices
               if (value := request.args.get(key, '')) in {v for v, _ in choices[key]}}
    typed = {key: value if key == 'state' else int(value) for key, value in filters.items()}
    return render_template('design/list.html', projects=design.projects.list_(db.session, actor, **typed),
                           choices=choices, selected=filters, applicability=_applicability(actor))


@bp.route('/<int:project_id>')
@login_required
@require_permission('read', RESOURCE)
def show_project(project_id: int) -> ResponseReturnValue:
    """Show the plan, stages, typed inputs/outputs and permitted lifecycle actions."""
    actor = current_actor()
    project = design.projects.get(db.session, actor, project_id)
    sections = []
    revisions = {}
    has_elements = False
    for section, (register, form_class, members, title) in IO_SECTIONS.items():
        all_rows = register.list_(db.session, actor, project_id=project_id)
        has_elements = has_elements or bool(all_rows)
        selected = request.args.get(section + '_tipo', '')
        if selected not in members.__members__:
            selected = ''
        rows = [row for row in all_rows if not selected or row.kind.name == selected]
        sections.append(dict(slug=section, title=title, rows=rows, members=members,
                             selected=selected))
        for row in rows:
            revision = design_io.visible_revision(db.session, actor, row.revision_id)
            if revision is not None:
                document = db.session.get(Document, revision.document_id)
                revisions[revision.id] = (document.code, revision.numero)
    plans = design_controls.plans.list_(db.session, actor, project_id=project_id)
    records = design_controls.records.list_(db.session, actor, project_id=project_id)
    resolved = {plan.id for plan in plans if design_controls.is_resolved(db.session, plan)}
    changes = design_changes.changes.list_(db.session, actor, project_id=project_id)
    return render_template(
        'design/detail.html', project=project,
        design_changes=changes,
        applied_changes={row.id for row in changes if design_changes.is_applied(db.session, row.id)},
        can_approve=design_changes.may_approve(db.session, actor, project),
        completion_blockers=design.completion_blockers(db.session, project),
        action_form=BaseForm(), rejection_form=DesignChangeRejectionForm(),
        control_plans=plans, control_records=records, resolved_controls=resolved,
        can_record_controls=project.state in design_controls.RECORD_STATES,
        io_sections=sections, visible_revisions=revisions,
        has_elements=has_elements or bool(plans) or bool(records) or bool(changes),
        stages=design.stages.list_(db.session, actor, project_id=project_id),
        targets=design.TRANSITIONS.get(project.state, ()) if design.may_transition(actor, project) else (),
        applicability=_applicability(actor), transition_form=DesignTransitionForm(),
        editable=project.state in design.TRANSITIONS)


STARTER_ONLY_ON_CREATE = ('Las etapas iniciales solo se crean al dar de alta el proyecto; '
                          'añade las etapas desde la ficha del proyecto.')


def _project_form(actor: Actor, project: DesignProject | None = None) -> DesignProjectForm:
    form = DesignProjectForm(obj=project)
    form.owner_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(project, 'owner_id', None)), empty=CHOOSE_PERSON)
    form.customer_id.choices = [('', '— Sin cliente —'),
                                *((c.id, c.nombre) for c in clientes.list_(db.session, actor))]
    form.process_ids.choices = [(p.id_proceso, p.proceso)
                                for p in process_operations.list_(db.session, actor)]
    if project is not None:
        del form['starter_stages']
        if request.method == 'GET':
            form.process_ids.data = project.process_ids
    return form


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_project() -> ResponseReturnValue:
    """Create a draft, offering but never silently creating initial stages."""
    actor = current_actor()
    form = _project_form(actor)
    if _control_payload_refused(form, 0):
        return render_template('design/form.html', form=form,
                               title='Nuevo proyecto de diseño'), 400
    if form.validate_on_submit() and (created := saved(lambda: design.projects.create(
            db.session, actor, form_data(form, PROJECT_FIELDS + ('starter_stages',))))):
        flash('Proyecto de diseño creado.', 'success')
        return _page(created.id)
    return render_template('design/form.html', form=form, title='Nuevo proyecto de diseño')


@bp.route('/<int:project_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_project(project_id: int) -> ResponseReturnValue:
    """Edit the plan without exposing its lifecycle state as form data."""
    actor = current_actor()
    project = design.projects.get(db.session, actor, project_id)
    if project.state not in design.TRANSITIONS:
        flash('El proyecto es de solo lectura.', 'danger')
        return _page(project_id)
    form = _project_form(actor, project)
    if request.method == 'POST' and 'starter_stages' in request.form:
        # The starter sequence is offered only on creation; never accept it silently here.
        form.validate_on_submit()
        flash(STARTER_ONLY_ON_CREATE, 'danger')
        return render_template('design/form.html', form=form,
                               title='Editar proyecto de diseño'), 400
    if _control_payload_refused(form, project_id):
        return render_template('design/form.html', form=form,
                               title='Editar proyecto de diseño'), 400
    if form.validate_on_submit() and saved(lambda: design.projects.update(
            db.session, actor, project_id, form_data(form, PROJECT_FIELDS))):
        flash('Proyecto de diseño actualizado.', 'success')
        return _page(project_id)
    return render_template('design/form.html', form=form, title='Editar proyecto de diseño')


@bp.route('/<int:project_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', RESOURCE)
def delete_project(project_id: int) -> ResponseReturnValue:
    """Delete an empty draft only, keeping its audit snapshot."""
    form = BaseForm()
    if _control_payload_refused(form, project_id):
        return _page(project_id), 400
    if form.validate_on_submit() and committed(lambda: design.projects.delete(db.session, current_actor(), project_id)):
        flash('Proyecto de diseño eliminado.', 'success')
        return redirect(url_for('design.list_projects'))
    return _page(project_id)


@bp.route('/<int:project_id>/transicion', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def transition_project(project_id: int) -> ResponseReturnValue:
    """Plan, start or cancel from explicit POST actions only."""
    form = DesignTransitionForm()
    if _control_payload_refused(form, project_id):
        return _page(project_id), 400
    if form.validate_on_submit() and committed(lambda: design.transition(
            db.session, current_actor(), project_id, form.target.data,
            cancellation_reason=form.cancellation_reason.data, today=local_today())):
        flash('Estado del proyecto actualizado.', 'success')
    return _page(project_id)


IO_SECTIONS = {
    'entradas': (design_io.inputs, DesignInputForm, DesignInputType, 'Entradas del diseño'),
    'salidas': (design_io.outputs, DesignOutputForm, DesignOutputType, 'Salidas del diseño'),
}


def _element(actor: Actor, section: str, project_id: int, element_id: int) -> Any:
    register = IO_SECTIONS[section][0]
    found = register.get(db.session, actor, element_id)
    if found.project_id != project_id:
        raise NotFound(register.SPEC.not_found)
    return found


def _element_form(actor: Actor, section: str, found: Any = None) -> Any:
    form_class = IO_SECTIONS[section][1]
    form = form_class(obj=found)
    form.revision_id.choices = design_io.revision_choices(
        db.session, actor, getattr(found, 'revision_id', None))
    return form


@bp.route('/<int:project_id>/<any(entradas,salidas):section>/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_element(project_id: int, section: str) -> ResponseReturnValue:
    """Add an input or output through the same policy and validation as MCP."""
    actor = current_actor()
    design.projects.get(db.session, actor, project_id)
    register = IO_SECTIONS[section][0]
    form = _element_form(actor, section)
    names = tuple(f.name for f in register.SPEC.fields if f.name != 'project_id')
    if _control_payload_refused(form, project_id):
        return render_template('design/io_form.html', form=form,
                               title='Añadir elemento de diseño', project_id=project_id), 400
    if form.validate_on_submit() and saved(lambda: register.create(
            db.session, actor, form_data(form, names) | {'project_id': project_id})):
        flash('Elemento de diseño añadido.', 'success')
        return _page(project_id)
    return render_template('design/io_form.html', form=form, title='Añadir elemento de diseño',
                           project_id=project_id)


@bp.route('/<int:project_id>/<any(entradas,salidas):section>/<int:element_id>/editar',
          methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_element(project_id: int, section: str, element_id: int) -> ResponseReturnValue:
    """Edit ordinary DD2 fields; rendered selectors retain stored enum/revision values."""
    actor = current_actor()
    found = _element(actor, section, project_id, element_id)
    register = IO_SECTIONS[section][0]
    form = _element_form(actor, section, found)
    names = tuple(f.name for f in register.SPEC.fields if f.name != 'project_id')
    if _control_payload_refused(form, project_id):
        return render_template('design/io_form.html', form=form,
                               title='Editar elemento de diseño', project_id=project_id), 400
    if form.validate_on_submit() and saved(lambda: register.update(
            db.session, actor, element_id, form_data(form, names))):
        flash('Elemento de diseño actualizado.', 'success')
        return _page(project_id)
    return render_template('design/io_form.html', form=form, title='Editar elemento de diseño',
                           project_id=project_id)


@bp.route('/<int:project_id>/<any(entradas,salidas):section>/<int:element_id>/eliminar',
          methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def delete_element(project_id: int, section: str, element_id: int) -> ResponseReturnValue:
    """Maintaining roles delete ordinary DD2 elements on the web only."""
    actor = current_actor()
    _element(actor, section, project_id, element_id)
    form = BaseForm()
    if _control_payload_refused(form, project_id):
        return _page(project_id), 400
    if form.validate_on_submit() and committed(lambda: IO_SECTIONS[section][0].delete(db.session, actor, element_id)):
        flash('Elemento de diseño eliminado.', 'success')
    return _page(project_id)


@bp.route('/revisiones/<int:revision_id>')
@login_required
@require_permission('read', RESOURCE)
def show_revision(revision_id: int) -> ResponseReturnValue:
    """Show the exact cited revision, never substituting today's effective revision."""
    actor = current_actor()
    if not design_io.is_cited(db.session, revision_id):
        # Only revisions cited by a design input or output are served here.
        raise NotFound('Revisión no encontrada.')
    revision = document_revisions.get(db.session, actor, revision_id)
    return render_template('design/revision.html', revision=revision,
                           document=db.session.get(Document, revision.document_id))


def _foreign_parent(project_id: int) -> int | None:
    """Refuse, never drop, a submitted project_id naming another project."""
    submitted = request.form.get('project_id') if request.method == 'POST' else None
    if submitted is None or submitted == str(project_id):
        return None
    flash(design_io.MOVE_REFUSED, 'danger')
    return 400


def _control_form(actor: Actor, project_id: int, *, record: bool = False,
                  found: Any = None) -> Any:
    """Populate all selectors, preserving the current plan's inactive person."""
    form = (DesignControlRecordForm if record else DesignPlannedControlForm)(obj=found)
    form.responsible_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(found, 'responsible_id', None)), empty=CHOOSE_PERSON)
    form.stage_id.choices = [('', '— Sin etapa —'), *((stage.id, stage.name) for stage in
        design.stages.list_(db.session, actor, project_id=project_id))]
    if record:
        form.planned_control_id.choices = [('', '— Sin control planificado —'), *(
            (plan.id, f'{plan.kind.value} · {plan.description}') for plan in
            design_controls.plans.list_(db.session, actor, project_id=project_id))]
    if request.method == 'GET' and found is None:
        form.kind.data = 'review'
        if record:
            form.result.data = 'conforming'
            form.date.data = local_today()
    return form


def _control_payload_refused(form: Any, project_id: int) -> bool:
    """Reject every explicitly submitted field the form would otherwise discard."""
    if _foreign_parent(project_id) is not None:
        return True
    if request.method == 'POST':
        # Uploaded files are submitted fields too; these forms accept none.
        unknown = ((set(request.form) - {field.name for field in form} - {'project_id'})
                   | set(request.files))
        if unknown:
            flash(f'Campos no permitidos: {", ".join(sorted(unknown))}.', 'danger')
            return True
        if any(len(request.form.getlist(key)) != 1
               and (key not in form or form[key].type != 'SelectMultipleField'
                    or len(set(request.form.getlist(key))) != len(request.form.getlist(key)))
               for key in request.form):
            flash('No repitas los campos del control.', 'danger')
            return True
    return False


def _control_page(form: Any, project_id: int, title: str) -> str:
    template = ('design/change_form.html' if isinstance(form, DesignChangeForm)
                else 'design/control_form.html')
    return render_template(template, form=form, project_id=project_id, title=title)


@bp.route('/<int:project_id>/controles/planes/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_control_plan(project_id: int) -> ResponseReturnValue:
    """Plan a separate review, verification or validation."""
    actor = current_actor()
    design.projects.get(db.session, actor, project_id)
    form = _control_form(actor, project_id)
    title = 'Planificar control del diseño'
    if _control_payload_refused(form, project_id):
        return _control_page(form, project_id, title), 400
    names = tuple(f.name for f in design_controls.PLAN_SPEC.fields
                  if f.name not in ('project_id', 'withdrawn', 'withdrawal_reason'))
    if form.validate_on_submit() and saved(lambda: design_controls.plans.create(
            db.session, actor, form_data(form, names) | {'project_id': project_id})):
        flash('Control del diseño planificado.', 'success')
        return _page(project_id)
    return _control_page(form, project_id, title)


def _control_plan(actor: Actor, project_id: int, plan_id: int) -> Any:
    found = design_controls.plans.get(db.session, actor, plan_id)
    if found.project_id != project_id:
        raise NotFound(design_controls.PLAN_SPEC.not_found)
    return found


@bp.route('/<int:project_id>/controles/planes/<int:plan_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_control_plan(project_id: int, plan_id: int) -> ResponseReturnValue:
    """Edit an unresolved plan; rendered no-op submissions never add audit rows."""
    actor = current_actor()
    found = _control_plan(actor, project_id, plan_id)
    form = _control_form(actor, project_id, found=found)
    title = 'Editar control planificado'
    if _control_payload_refused(form, project_id):
        return _control_page(form, project_id, title), 400
    names = tuple(f.name for f in design_controls.PLAN_SPEC.fields
                  if f.name not in ('project_id', 'withdrawn', 'withdrawal_reason'))
    if form.validate_on_submit() and saved(lambda: design_controls.plans.update(
            db.session, actor, plan_id, form_data(form, names))):
        flash('Control planificado actualizado.', 'success')
        return _page(project_id)
    return _control_page(form, project_id, title)


@bp.route('/<int:project_id>/controles/planes/<int:plan_id>/retirar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def withdraw_control_plan(project_id: int, plan_id: int) -> ResponseReturnValue:
    """Resolve an omitted planned control only through a justified, CSRF POST."""
    actor = current_actor()
    _control_plan(actor, project_id, plan_id)
    form = DesignControlWithdrawalForm()
    title = 'Retirar control planificado'
    if _control_payload_refused(form, project_id):
        return _control_page(form, project_id, title), 400
    if form.validate_on_submit() and saved(lambda: design_controls.plans.update(
            db.session, actor, plan_id, {'withdrawn': True,
                                        'withdrawal_reason': form.withdrawal_reason.data})):
        flash('Control planificado retirado con justificación.', 'success')
        return _page(project_id)
    return _control_page(form, project_id, title)


@bp.route('/<int:project_id>/controles/registro/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_control_record(project_id: int) -> ResponseReturnValue:
    """Append immutable control evidence; no edit/delete routes exist."""
    actor = current_actor()
    design.projects.get(db.session, actor, project_id)
    form = _control_form(actor, project_id, record=True)
    title = 'Registrar control del diseño'
    if _control_payload_refused(form, project_id):
        return _control_page(form, project_id, title), 400
    names = tuple(f.name for f in design_controls.RECORD_SPEC.fields if f.name != 'project_id')
    if form.validate_on_submit() and saved(lambda: design_controls.records.create(
            db.session, actor, form_data(form, names) | {'project_id': project_id},
            today=local_today())):
        flash('Control del diseño registrado; la evidencia no se modifica ni se elimina.', 'success')
        return _page(project_id)
    return _control_page(form, project_id, title)


def _change_form(actor: Actor, project_id: int, found: Any = None) -> DesignChangeForm:
    """Offer approved current versions, retaining selected links on a GET round-trip."""
    form = DesignChangeForm(obj=found)
    tips = {v.output_id for v in design_changes.current_versions(db.session, project_id)
            if v.state.name == 'approved'}
    retained = set(found.output_ids) if found is not None else set()
    rows = design_io.outputs.list_(db.session, actor, project_id=project_id)
    form.output_ids.choices = [(row.id, f'{row.kind.value} · #{row.lineage_id} · Versión {row.version}')
                              for row in rows if row.id in tips | retained]
    if request.method == 'GET' and found is not None:
        form.output_ids.data = found.output_ids
    return form


def _change(actor: Actor, project_id: int, change_id: int) -> Any:
    """Never serve a child action through a foreign project URL."""
    row = design_changes.changes.get(db.session, actor, change_id)
    if row.project_id != project_id:
        raise NotFound(design_changes.CHANGE_SPEC.not_found)
    return row


@bp.route('/<int:project_id>/cambios/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_change(project_id: int) -> ResponseReturnValue:
    """Propose a design change; no approval state is accepted as form data."""
    actor = current_actor()
    design.projects.get(db.session, actor, project_id)
    form = _change_form(actor, project_id)
    title = 'Proponer cambio del diseño'
    if _control_payload_refused(form, project_id):
        return _control_page(form, project_id, title), 400
    names = tuple(f.name for f in design_changes.CHANGE_SPEC.fields if f.name != 'project_id')
    if form.validate_on_submit() and saved(lambda: design_changes.changes.create(
            db.session, actor, form_data(form, names) | {'project_id': project_id})):
        flash('Cambio del diseño propuesto.', 'success')
        return _page(project_id)
    return _control_page(form, project_id, title)


@bp.route('/<int:project_id>/cambios/<int:change_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_change(project_id: int, change_id: int) -> ResponseReturnValue:
    """Edit proposed changes, with all selectors preselected on GET."""
    actor = current_actor()
    found = _change(actor, project_id, change_id)
    form = _change_form(actor, project_id, found)
    title = 'Editar cambio del diseño'
    if _control_payload_refused(form, project_id):
        return _control_page(form, project_id, title), 400
    names = tuple(f.name for f in design_changes.CHANGE_SPEC.fields if f.name != 'project_id')
    if form.validate_on_submit() and saved(lambda: design_changes.changes.update(
            db.session, actor, change_id, form_data(form, names))):
        flash('Cambio del diseño actualizado.', 'success')
        return _page(project_id)
    return _control_page(form, project_id, title)


@bp.route('/<int:project_id>/salidas/<int:element_id>/aprobar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def approve_output(project_id: int, element_id: int) -> ResponseReturnValue:
    """Approve an exact output version, never an arbitrary posted identifier."""
    actor = current_actor()
    _element(actor, 'salidas', project_id, element_id)
    form = BaseForm()
    if _control_payload_refused(form, project_id):
        return _page(project_id), 400
    if form.validate_on_submit() and committed(lambda: design_changes.approve_output(
            db.session, actor, element_id, today=local_today())):
        flash('Salida del diseño aprobada.', 'success')
    return _page(project_id)


@bp.route('/<int:project_id>/cambios/<int:change_id>/<any(autorizar,rechazar,aplicar):action>',
          methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def decide_change(project_id: int, change_id: int, action: str) -> ResponseReturnValue:
    """Authorize/reject as independent administrator, or apply as a maintaining role."""
    actor = current_actor()
    _change(actor, project_id, change_id)
    form = DesignChangeRejectionForm() if action == 'rechazar' else BaseForm()
    if _control_payload_refused(form, project_id):
        return _page(project_id), 400
    operations = {
        'autorizar': lambda: design_changes.authorize(db.session, actor, change_id, today=local_today()),
        'rechazar': lambda: design_changes.reject(db.session, actor, change_id, reason=form.reason.data),
        'aplicar': lambda: design_changes.apply(db.session, actor, change_id),
    }
    if form.validate_on_submit() and committed(operations[action]):
        flash('Cambio del diseño actualizado.', 'success')
    return _page(project_id)


@bp.route('/<int:project_id>/completar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def complete_project(project_id: int) -> ResponseReturnValue:
    """Complete only through the locked, independent-administrator service gate."""
    form = BaseForm()
    if _control_payload_refused(form, project_id):
        return _page(project_id), 400
    if form.validate_on_submit() and committed(lambda: design.complete(
            db.session, current_actor(), project_id, today=local_today())):
        flash('Proyecto de diseño completado con aprobación.', 'success')
    return _page(project_id)


def _stage(actor: Actor, project_id: int, stage_id: int) -> DesignStage:
    found = design.stages.get(db.session, actor, stage_id)
    if found.project_id != project_id:
        raise NotFound(design.STAGE_SPEC.not_found)
    return found


def _stage_form(actor: Actor, stage: DesignStage | None = None) -> DesignStageForm:
    form = DesignStageForm(obj=stage)
    form.owner_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(stage, 'owner_id', None)), empty=CHOOSE_PERSON)
    return form


@bp.route('/<int:project_id>/etapas/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_stage(project_id: int) -> ResponseReturnValue:
    """Add an editable stage to the project."""
    actor = current_actor()
    design.projects.get(db.session, actor, project_id)
    form = _stage_form(actor)
    if _control_payload_refused(form, project_id):
        return render_template('design/form.html', form=form,
                               title='Nueva etapa', project_id=project_id), 400
    data = form_data(form, STAGE_FIELDS)
    if data['position'] is None:
        data.pop('position')
    if form.validate_on_submit() and saved(lambda: design.stages.create(
            db.session, actor, data | {'project_id': project_id}, today=local_today())):
        flash('Etapa añadida.', 'success')
        return _page(project_id)
    return render_template('design/form.html', form=form, title='Nueva etapa', project_id=project_id)


@bp.route('/<int:project_id>/etapas/<int:stage_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_stage(project_id: int, stage_id: int) -> ResponseReturnValue:
    """Edit or reorder an unfinished stage using an explicit 1-based position."""
    actor = current_actor()
    stage = _stage(actor, project_id, stage_id)
    form = _stage_form(actor, stage)
    if _control_payload_refused(form, project_id):
        return render_template('design/form.html', form=form,
                               title='Editar etapa', project_id=project_id), 400
    data = form_data(form, STAGE_FIELDS)
    if data['position'] is None:
        data.pop('position')
    if form.validate_on_submit() and saved(lambda: design.stages.update(
            db.session, actor, stage_id, data, today=local_today())):
        flash('Etapa actualizada.', 'success')
        return _page(project_id)
    return render_template('design/form.html', form=form, title='Editar etapa', project_id=project_id)


@bp.route('/<int:project_id>/etapas/<int:stage_id>/eliminar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def delete_stage(project_id: int, stage_id: int) -> ResponseReturnValue:
    """Both maintaining roles may delete unfinished stages, never evidence."""
    actor = current_actor()
    _stage(actor, project_id, stage_id)
    form = BaseForm()
    if _control_payload_refused(form, project_id):
        return _page(project_id), 400
    if form.validate_on_submit() and committed(lambda: design.stages.delete(db.session, actor, stage_id)):
        flash('Etapa eliminada.', 'success')
    return _page(project_id)
