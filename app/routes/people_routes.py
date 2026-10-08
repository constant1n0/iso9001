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

"""People screens (ISO 9001 clauses 5.3 and 7.2): thin adapters over ``services.people``.

Every role reads the list and a person's page, which also lists the person's
competence records (``services.competence``); administrators and auditors
create and edit; administrators delete (policy ``PEOPLE``, decision Q1 of
``qms-people``). The form picks the roles held and the linked user account;
only accounts not linked to another person are offered, which needs the
``USERS`` read grant that every writer of people holds. Account names are
shown to those who may read users; others only see that a link exists.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on
the re-rendered form, which keeps what was typed. Deleting a person that other
records cite is refused with a flash (deactivate it instead). Other domain
errors reach the global handlers (an unknown id is a 404, a policy refusal
redirects to the dashboard).

``form_data``, ``saved``, ``all_roles`` and ``requirement_labels`` are shared
with the competence screens.
"""

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required
from flask_wtf import FlaskForm

from ..extensions import db
from ..forms import PersonForm, role_choices, user_choices
from ..models import Person, RolResponsabilidad
from ..services import competence, crud, people, roles_responsibilities, users
from ..services.actor import Actor
from ..services.errors import Conflict, ValidationError
from ..utils.permissions import can, require_permission
from ..utils.web_actor import current_actor

bp = Blueprint('people', __name__, url_prefix='/personas')

FORM_FIELDS = ('nombre', 'email', 'rol_ids', 'user_id', 'activo', 'notas')
ACTIVE_FILTER = {'1': True, '0': False}  # the list's «Estado» filter

T = TypeVar('T')


def form_data(form: FlaskForm, names: Iterable[str]) -> dict[str, Any]:
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in names}


def saved(write: Callable[[], T]) -> T | None:
    """Run a service write that returns its record, commit, and return the record.

    On a ``ValidationError`` or ``Conflict`` roll back, flash why and return
    ``None``; the route then shows the form again with what was typed.
    """
    try:
        written = write()
        db.session.commit()
    except (ValidationError, Conflict) as error:
        db.session.rollback()
        flash(error.message, 'danger')
        return None
    return written


def all_roles(actor: Actor) -> list[RolResponsabilidad]:
    """Every QMS role, by name, for the selects and the labels."""
    roles = crud.list_(roles_responsibilities.SPEC, db.session, actor)
    return sorted(roles, key=lambda role: role.rol.casefold())


def requirement_labels(actor: Actor) -> dict[int, str]:
    """Every competence requirement as «role · description», by id."""
    role_names = {role.id_rol: role.rol for role in all_roles(actor)}
    return {
        requirement.id: f'{role_names.get(requirement.rol_id, "—")} · {requirement.descripcion}'
        for requirement in competence.requirements.list_(db.session, actor)
    }


def _usernames(actor: Actor) -> dict[int, str]:
    """Account names by user id for those who may read users, else nothing."""
    if not can('read', 'users'):
        return {}
    return {user.id: user.username for user in users.list_(db.session, actor)}


def _form(actor: Actor, person: Person | None = None) -> PersonForm:
    """The person form with its role and user choices.

    A user linked to another person is not offered; the edited person's own
    link stays available.
    """
    form = PersonForm(obj=person)
    own_id = person.id if person is not None else None
    taken = {other.user_id for other in people.list_(db.session, actor)
             if other.user_id is not None and other.id != own_id}
    form.rol_ids.choices = role_choices(all_roles(actor))
    form.user_id.choices = user_choices(
        user for user in users.list_(db.session, actor) if user.id not in taken
    )
    return form


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'people')
def list_people() -> ResponseReturnValue:
    """People matching the filters; ``applied`` counts only the filters that apply.

    A blank ``nombre``, an ``activo`` outside ``ACTIVE_FILTER`` or a ``rol_id``
    that is not a number is ignored, so it neither filters nor counts.
    """
    actor = current_actor()
    nombre = request.args.get('nombre', '').strip()
    activo = request.args.get('activo', '')
    activo = activo if activo in ACTIVE_FILTER else ''
    rol_id = request.args.get('rol_id', type=int)
    found = people.list_(db.session, actor, nombre=nombre or None,
                         activo=ACTIVE_FILTER.get(activo), rol_id=rol_id)
    applied = sum((bool(nombre), bool(activo), rol_id is not None))
    return render_template('people/list.html', people=found, roles=all_roles(actor),
                           rol_id=rol_id, activo=activo, applied=applied,
                           usernames=_usernames(actor))


@bp.route('/<int:person_id>', methods=['GET'])
@login_required
@require_permission('read', 'people')
def show_person(person_id: int) -> ResponseReturnValue:
    actor = current_actor()
    person = people.get(db.session, actor, person_id)
    records = competence.records.list_(db.session, actor, persona_id=person.id)
    return render_template('people/detail.html', person=person, records=records,
                           requirements=requirement_labels(actor),
                           usernames=_usernames(actor))


@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'people')
def new_person() -> ResponseReturnValue:
    actor = current_actor()
    form = _form(actor)
    created = form.validate_on_submit() and saved(
        lambda: people.create(db.session, actor, form_data(form, FORM_FIELDS))
    )
    if created:
        flash('Persona creada exitosamente', 'success')
        return redirect(url_for('people.show_person', person_id=created.id))
    return render_template('people/form.html', form=form, person=None)


@bp.route('/<int:person_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'people')
def edit_person(person_id: int) -> ResponseReturnValue:
    actor = current_actor()
    person = people.get(db.session, actor, person_id)
    form = _form(actor, person)
    if form.validate_on_submit() and saved(
        lambda: people.update(db.session, actor, person_id, form_data(form, FORM_FIELDS))
    ):
        flash('Persona actualizada exitosamente', 'success')
        return redirect(url_for('people.show_person', person_id=person_id))
    return render_template('people/form.html', form=form, person=person)


@bp.route('/<int:person_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'people')
def delete_person(person_id: int) -> ResponseReturnValue:
    """Delete a person; one that other records cite stays, with a flash saying why."""
    try:
        people.delete(db.session, current_actor(), person_id)
        db.session.commit()
    except Conflict as error:
        db.session.rollback()
        flash(error.message, 'danger')
        return redirect(url_for('people.show_person', person_id=person_id))
    flash('Persona eliminada exitosamente', 'success')
    return redirect(url_for('people.list_people'))
