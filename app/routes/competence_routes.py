# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""Competence screens (ISO 9001 clause 7.2): thin adapters over ``services.competence``.

Two registers under ``/competencias``: the competence each role requires
(``/requisitos/``, filtered by role) and the competence each person has
demonstrated, whose forms are reached from the person's page and return there.
``/matriz`` compares both for every role and its active holders, against
today in the application's time zone. Every role reads; administrators and
auditors create and edit; administrators delete (policy ``COMPETENCE``,
decision Q1 of ``qms-people``).

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed. A refused delete (a requirement
that records cite, or any other ``Conflict``) is rolled back and flashed. Other
domain errors reach the global handlers (an unknown id is a 404, a policy
refusal redirects to the dashboard).
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (
    NO_ROLE, CompetenceRecordForm, CompetenceRequirementForm, person_choices,
    requirement_choices, role_choices, training_choices,
)
from ..models import CompetenceRecord, CompetenceRequirement
from ..services import competence, people, training
from ..services.actor import Actor
from ..services.errors import Conflict
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .people_routes import all_roles, form_data, requirement_labels, saved

bp = Blueprint('competence', __name__, url_prefix='/competencias')

REQUIREMENT_FIELDS = ('rol_id', 'tipo', 'descripcion', 'criterio')
RECORD_FIELDS = (
    'requisito_id', 'evidencia', 'capacitacion_id', 'fecha_obtencion', 'fecha_caducidad',
    'evaluacion_eficacia', 'fecha_evaluacion', 'evaluador_id',
)


# -- requirements per role ------------------------------------------------------


def _requirement_form(
    actor: Actor, requirement: CompetenceRequirement | None = None
) -> CompetenceRequirementForm:
    """The requirement form with every role to choose from."""
    form = CompetenceRequirementForm(obj=requirement)
    form.rol_id.choices = [NO_ROLE, *role_choices(all_roles(actor))]
    if requirement is not None and request.method == 'GET':
        form.tipo.data = requirement.tipo.name  # the select uses member names
    return form


@bp.route('/requisitos/', methods=['GET'])
@login_required
@require_permission('read', 'competence')
def list_requirements() -> ResponseReturnValue:
    actor = current_actor()
    rol_id = request.args.get('rol_id', type=int)
    roles = all_roles(actor)
    role_names = {role.id_rol: role.rol for role in roles}
    found = sorted(competence.requirements.list_(db.session, actor, rol_id=rol_id),
                   key=lambda r: (role_names.get(r.rol_id, '').casefold(), r.id))
    return render_template('competence/requirements.html', requirements=found, roles=roles,
                           role_names=role_names, rol_id=rol_id)


@bp.route('/requisitos/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'competence')
def new_requirement() -> ResponseReturnValue:
    actor = current_actor()
    form = _requirement_form(actor)
    created = form.validate_on_submit() and saved(
        lambda: competence.requirements.create(db.session, actor,
                                               form_data(form, REQUIREMENT_FIELDS))
    )
    if created:
        flash('Requisito de competencia creado exitosamente', 'success')
        return redirect(url_for('competence.list_requirements', rol_id=created.rol_id))
    return render_template('competence/form.html', form=form, requirement=None)


@bp.route('/requisitos/<int:requirement_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'competence')
def edit_requirement(requirement_id: int) -> ResponseReturnValue:
    actor = current_actor()
    requirement = competence.requirements.get(db.session, actor, requirement_id)
    form = _requirement_form(actor, requirement)
    updated = form.validate_on_submit() and saved(
        lambda: competence.requirements.update(db.session, actor, requirement_id,
                                               form_data(form, REQUIREMENT_FIELDS))
    )
    if updated:
        flash('Requisito de competencia actualizado exitosamente', 'success')
        return redirect(url_for('competence.list_requirements', rol_id=updated.rol_id))
    return render_template('competence/form.html', form=form,
                           requirement=requirement)


@bp.route('/requisitos/<int:requirement_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'competence')
def delete_requirement(requirement_id: int) -> ResponseReturnValue:
    """Delete a requirement; one that records cite stays, with a flash saying why."""
    try:
        competence.requirements.delete(db.session, current_actor(), requirement_id)
        db.session.commit()
    except Conflict as error:
        db.session.rollback()
        flash(error.message, 'danger')
    else:
        flash('Requisito de competencia eliminado exitosamente', 'success')
    return redirect(url_for('competence.list_requirements'))


@bp.route('/matriz', methods=['GET'])
@login_required
@require_permission('read', 'competence')
def matrix() -> ResponseReturnValue:
    """Required versus demonstrated competence, optionally for one role."""
    actor = current_actor()
    rol_id = request.args.get('rol_id', type=int)
    groups = competence.matrix(db.session, actor, rol_id=rol_id, today=local_today())
    return render_template('competence/matrix.html', groups=groups, roles=all_roles(actor),
                           rol_id=rol_id)


# -- records of a person ----------------------------------------------------------


def _record_form(actor: Actor, record: CompetenceRecord | None = None) -> CompetenceRecordForm:
    """The record form; the evaluator select keeps the stored one even when inactive."""
    form = CompetenceRecordForm(obj=record)
    form.requisito_id.choices = requirement_choices(requirement_labels(actor))
    form.capacitacion_id.choices = training_choices(training.list_(db.session, actor))
    form.evaluador_id.choices = person_choices(people.choices(
        db.session, actor, include=record.evaluador_id if record is not None else None))
    if record is not None and request.method == 'GET':
        form.evaluacion_eficacia.data = record.evaluacion_eficacia.name
    return form


@bp.route('/personas/<int:person_id>/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'competence')
def new_record(person_id: int) -> ResponseReturnValue:
    actor = current_actor()
    person = people.get(db.session, actor, person_id)
    form = _record_form(actor)
    if form.validate_on_submit() and saved(
        lambda: competence.records.create(
            db.session, actor, form_data(form, RECORD_FIELDS) | {'persona_id': person_id})
    ):
        flash('Competencia registrada exitosamente', 'success')
        return redirect(url_for('people.show_person', person_id=person_id))
    return render_template('competence/form.html', form=form, person=person,
                           record=None)


@bp.route('/<int:record_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'competence')
def edit_record(record_id: int) -> ResponseReturnValue:
    actor = current_actor()
    record = competence.records.get(db.session, actor, record_id)
    person_id = record.persona_id
    person = people.get(db.session, actor, person_id)
    form = _record_form(actor, record)
    if form.validate_on_submit() and saved(
        lambda: competence.records.update(db.session, actor, record_id,
                                          form_data(form, RECORD_FIELDS))
    ):
        flash('Competencia actualizada exitosamente', 'success')
        return redirect(url_for('people.show_person', person_id=person_id))
    return render_template('competence/form.html', form=form, person=person,
                           record=record)


@bp.route('/<int:record_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'competence')
def delete_record(record_id: int) -> ResponseReturnValue:
    """Delete a record; a refused delete is flashed on the person's page."""
    actor = current_actor()
    person_id = competence.records.get(db.session, actor, record_id).persona_id
    try:
        competence.records.delete(db.session, actor, record_id)
        db.session.commit()
    except Conflict as error:
        db.session.rollback()
        flash(error.message, 'danger')
    else:
        flash('Competencia eliminada exitosamente', 'success')
    return redirect(url_for('people.show_person', person_id=person_id))
