"""CSRF-protected design projects, stages, inputs and outputs (DD1–DD2)."""
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (CHOOSE_PERSON, DesignInputForm, DesignOutputForm,
                     DesignProjectForm, DesignStageForm, DesignTransitionForm,
                     person_choices)
from ..models import (DesignInputType, DesignOutputType, DesignProject,
                      DesignStage, DesignState, Document)
from ..services import (clientes, design, design_io, document_revisions,
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
    return render_template(
        'design/detail.html', project=project,
        io_sections=sections, visible_revisions=revisions, has_elements=has_elements,
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
    if (refused := _foreign_parent(project_id)) is not None:
        return render_template('design/io_form.html', form=form,
                               title='Añadir elemento de diseño', project_id=project_id), refused
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
    if (refused := _foreign_parent(project_id)) is not None:
        return render_template('design/io_form.html', form=form,
                               title='Editar elemento de diseño', project_id=project_id), refused
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
    if committed(lambda: IO_SECTIONS[section][0].delete(db.session, actor, element_id)):
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
