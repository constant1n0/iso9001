"""Thin, CSRF-protected web adapters for design projects and stages (DD1)."""
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (CHOOSE_PERSON, DesignProjectForm, DesignStageForm,
                     DesignTransitionForm, person_choices)
from ..models import DesignProject, DesignStage, DesignState
from ..services import clientes, design, people, process_operations, qms_scope
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
    """Show the design plan, ordered stages and allowed lifecycle actions."""
    actor = current_actor()
    project = design.projects.get(db.session, actor, project_id)
    return render_template(
        'design/detail.html', project=project,
        stages=design.stages.list_(db.session, actor, project_id=project_id),
        targets=design.TRANSITIONS.get(project.state, ()) if design.may_transition(actor, project) else (),
        applicability=_applicability(actor), transition_form=DesignTransitionForm(),
        editable=project.state in design.TRANSITIONS)


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
    if committed(lambda: design.projects.delete(db.session, current_actor(), project_id)):
        flash('Proyecto de diseño eliminado.', 'success')
        return redirect(url_for('design.list_projects'))
    return _page(project_id)


@bp.route('/<int:project_id>/transicion', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def transition_project(project_id: int) -> ResponseReturnValue:
    """Plan, start or cancel from explicit POST actions only."""
    form = DesignTransitionForm()
    if form.validate_on_submit() and committed(lambda: design.transition(
            db.session, current_actor(), project_id, form.target.data,
            cancellation_reason=form.cancellation_reason.data)):
        flash('Estado del proyecto actualizado.', 'success')
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
    if committed(lambda: design.stages.delete(db.session, actor, stage_id)):
        flash('Etapa eliminada.', 'success')
    return _page(project_id)
