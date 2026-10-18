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

"""Process and role screens (ISO 9001 clauses 4.4, 5.3 and 8.1): adapters over two services.

Task RG-2 of ``process-role-registers``. Processes live under ``/procesos``
(blueprint ``proceso``) and roles under ``/roles`` (``rol``); the JSON
registers at ``/proceso_operacion/`` and ``/rol_responsabilidad/`` stay as
they are. Every role reads both; administrators and auditors create and edit;
administrators delete (policies ``PROCESS_OPERATIONS`` and
``ROLES_RESPONSIBILITIES``, decision PR5).

- The process list, by name, filters by type and owner (a value that names
  nothing is ignored, so it neither filters nor counts) and counts each
  process's related processes and the quality objectives and the risks or
  opportunities citing it (``process_operations.counts``).
- A process's page shows every field, its related processes (the relation is
  mutual) and the records citing it, each linked (PR2). The related-process
  picker leaves out the process itself; picking none removes every relation.
- The role list, by name, shows whom each role reports to, how many people
  hold it (inactive ones included) and its quality commitment. A role's page
  shows its texts, its reporting line from the role it reports to up to the
  top, the roles reporting directly to it, the people holding it and its
  competence requirements (PR3). The "reports to" picker leaves out the role
  itself; a choice that would close a cycle is refused by the service.
- A process related to or cited by other records, and a role with competence
  requirements or subordinates, offer no delete; the service refuses it anyway.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed; a refused delete is flashed on
the record's page.
"""

from collections import Counter

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..extensions import db
from ..forms import NO_MANAGER, ProcesoForm, RolForm, person_choices
from ..models import ProcesoOperacion, RolResponsabilidad, TipoProceso
from ..services import people, process_operations, roles_responsibilities
from ..services.actor import Actor
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import all_roles, form_data, saved

processes_bp = Blueprint('proceso', __name__, url_prefix='/procesos')
roles_bp = Blueprint('rol', __name__, url_prefix='/roles')

PROCESSES, ROLES = 'process_operations', 'roles_responsibilities'
PROCESS_FIELDS = ('proceso', 'tipo', 'responsable_id', 'relacionados', 'entradas', 'salidas',
                  'recursos', 'criterio_calidad', 'criterios_aceptacion', 'control_proveedor',
                  'no_conformidad')
ROLE_FIELDS = ('rol', 'reporta_a_id', 'responsabilidades', 'autoridades', 'compromiso_calidad',
               'descripcion_politica_calidad')
TYPES = [(member.name, member.value) for member in TipoProceso]


# -- processes -------------------------------------------------------------------------


def _process_page(process_id: int) -> ResponseReturnValue:
    return redirect(url_for('proceso.show_process', process_id=process_id))


def _by_name(found: list[ProcesoOperacion]) -> list[ProcesoOperacion]:
    return sorted(found, key=lambda process: (process.proceso.casefold(), process.id_proceso))


@processes_bp.route('/', methods=['GET'])
@login_required
@require_permission('read', PROCESSES)
def list_processes() -> ResponseReturnValue:
    """Processes matching the filters with their counts; ``applied`` counts the filters used."""
    actor = current_actor()
    every = _by_name(process_operations.list_(db.session, actor))
    owners = people.names(db.session, actor, (p.responsable_id for p in every))
    fields = [('tipo', 'Tipo', TYPES),
              ('responsable_id', 'Responsable',
               sorted(((str(person_id), nombre) for person_id, nombre in owners.items()),
                      key=lambda choice: choice[1].casefold()))]
    chosen = {}
    for name, _, choices in fields:
        text = request.args.get(name, '')
        if text in {value for value, _ in choices}:
            chosen[name] = text
    found = every
    if chosen:
        owner = chosen.get('responsable_id')
        found = _by_name(process_operations.list_(
            db.session, actor, tipo=chosen.get('tipo'),
            responsable_id=None if owner is None else int(owner)))
    return render_template('procesos/list.html', procesos=found, personas=owners,
                           cuentas=process_operations.counts(db.session, actor),
                           campos=fields, elegidos=chosen, applied=len(chosen))


@processes_bp.route('/<int:process_id>', methods=['GET'])
@login_required
@require_permission('read', PROCESSES)
def show_process(process_id: int) -> ResponseReturnValue:
    """A process's page: its fields, related processes and the records citing it."""
    actor = current_actor()
    proceso = process_operations.get(db.session, actor, process_id)
    relacionados = process_operations.related(db.session, actor, process_id)
    citas = process_operations.cited_by(db.session, actor, process_id)
    cited = citas.objetivos or citas.riesgos or citas.oportunidades
    return render_template(
        'procesos/detail.html', proceso=proceso, relacionados=relacionados, citas=citas,
        responsable=people.names(db.session, actor, [proceso.responsable_id]).get(
            proceso.responsable_id),
        eliminable=not relacionados and not cited)


def _process_form(actor: Actor, proceso: ProcesoOperacion | None = None) -> ProcesoForm:
    """The process form; the pickers keep the stored owner and leave out the process."""
    form = ProcesoForm(obj=proceso)
    own = getattr(proceso, 'id_proceso', None)
    form.responsable_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(proceso, 'responsable_id', None)))
    form.relacionados.choices = [(p.id_proceso, p.proceso) for p in _by_name(
        process_operations.list_(db.session, actor)) if p.id_proceso != own]
    if own is not None and request.method == 'GET':
        form.relacionados.data = process_operations.related_ids(db.session, own)
    return form


@processes_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', PROCESSES)
def new_process() -> ResponseReturnValue:
    actor = current_actor()
    form = _process_form(actor)
    if form.validate_on_submit() and (created := saved(lambda: process_operations.create(
            db.session, actor, form_data(form, PROCESS_FIELDS)))):
        flash(f'Proceso «{created.proceso}» creado.', 'success')
        return _process_page(created.id_proceso)
    return render_template('procesos/form.html', form=form, proceso=None)


@processes_bp.route('/<int:process_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', PROCESSES)
def edit_process(process_id: int) -> ResponseReturnValue:
    """Edit a process; the related processes picked replace the current ones."""
    actor = current_actor()
    proceso = process_operations.get(db.session, actor, process_id)
    form = _process_form(actor, proceso)
    if form.validate_on_submit() and saved(lambda: process_operations.update(
            db.session, actor, process_id, form_data(form, PROCESS_FIELDS))):
        flash('Proceso actualizado.', 'success')
        return _process_page(process_id)
    return render_template('procesos/form.html', form=form, proceso=proceso)


@processes_bp.route('/<int:process_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', PROCESSES)
def delete_process(process_id: int) -> ResponseReturnValue:
    """Delete a process nothing relates to or cites (administrators)."""
    if committed(lambda: process_operations.delete(db.session, current_actor(), process_id)):
        flash('Proceso eliminado.', 'success')
        return redirect(url_for('proceso.list_processes'))
    return _process_page(process_id)


# -- roles -----------------------------------------------------------------------------


def _role_page(rol_id: int) -> ResponseReturnValue:
    return redirect(url_for('rol.show_role', rol_id=rol_id))


def _line(rol: RolResponsabilidad,
          by_id: dict[int, RolResponsabilidad]) -> list[RolResponsabilidad]:
    """The roles above ``rol``: the one it reports to first, then up to the top.

    The service keeps the line free of cycles; one stored all the same (two
    concurrent updates) ends the walk instead of looping.
    """
    line: list[RolResponsabilidad] = []
    seen, step = {rol.id_rol}, rol.reporta_a_id
    while step in by_id and step not in seen:
        seen.add(step)
        line.append(by_id[step])
        step = by_id[step].reporta_a_id
    return line


@roles_bp.route('/', methods=['GET'])
@login_required
@require_permission('read', ROLES)
def list_roles() -> ResponseReturnValue:
    """Every role by name, whom it reports to and how many people hold it."""
    actor = current_actor()
    found = all_roles(actor)
    holders = Counter(role.id_rol for person in people.list_(db.session, actor)
                      for role in person.roles)
    return render_template('roles/list.html', roles=found, titulares=holders,
                           nombres={role.id_rol: role.rol for role in found})


@roles_bp.route('/<int:rol_id>', methods=['GET'])
@login_required
@require_permission('read', ROLES)
def show_role(rol_id: int) -> ResponseReturnValue:
    """A role's page: its texts, reporting line, subordinates, holders and requirements."""
    actor = current_actor()
    rol = roles_responsibilities.get(db.session, actor, rol_id)
    holders = roles_responsibilities.holders(db.session, actor, rol_id)
    every = all_roles(actor)
    subordinados = [role for role in every if role.reporta_a_id == rol.id_rol]
    return render_template(
        'roles/detail.html', rol=rol, linea=_line(rol, {role.id_rol: role for role in every}),
        subordinados=subordinados, personas=holders.personas, requisitos=holders.requisitos,
        eliminable=not subordinados and not holders.requisitos)


def _role_form(actor: Actor, rol: RolResponsabilidad | None = None) -> RolForm:
    """The role form; the "reports to" picker leaves out the role itself."""
    form = RolForm(obj=rol)
    own = getattr(rol, 'id_rol', None)
    form.reporta_a_id.choices = [NO_MANAGER, *((role.id_rol, role.rol)
                                               for role in all_roles(actor) if role.id_rol != own)]
    return form


@roles_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', ROLES)
def new_role() -> ResponseReturnValue:
    actor = current_actor()
    form = _role_form(actor)
    if form.validate_on_submit() and (created := saved(lambda: roles_responsibilities.create(
            db.session, actor, form_data(form, ROLE_FIELDS)))):
        flash(f'Rol «{created.rol}» creado.', 'success')
        return _role_page(created.id_rol)
    return render_template('roles/form.html', form=form, rol=None)


@roles_bp.route('/<int:rol_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', ROLES)
def edit_role(rol_id: int) -> ResponseReturnValue:
    """Edit a role; a "reports to" that closes a cycle is flashed on the form."""
    actor = current_actor()
    rol = roles_responsibilities.get(db.session, actor, rol_id)
    form = _role_form(actor, rol)
    if form.validate_on_submit() and saved(lambda: roles_responsibilities.update(
            db.session, actor, rol_id, form_data(form, ROLE_FIELDS))):
        flash('Rol actualizado.', 'success')
        return _role_page(rol_id)
    return render_template('roles/form.html', form=form, rol=rol)


@roles_bp.route('/<int:rol_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', ROLES)
def delete_role(rol_id: int) -> ResponseReturnValue:
    """Delete a role without requirements or subordinates (administrators)."""
    if committed(lambda: roles_responsibilities.delete(db.session, current_actor(), rol_id)):
        flash('Rol eliminado.', 'success')
        return redirect(url_for('rol.list_roles'))
    return _role_page(rol_id)
