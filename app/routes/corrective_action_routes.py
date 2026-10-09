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

"""Corrective action screens (ISO 9001 clause 10.2): adapters over ``corrective_actions``.

The URLs live under their nonconformity (``/no_conformidades/<nc_id>/acciones/…``)
and every page returns to the nonconformity's page, which lists the actions.
Policy ``CORRECTIVE_ACTIONS``: every role adds and edits, administrators
delete; verifying is further limited to administrators and auditors
(``may_verify``), and the verifier picker never offers the action's owner.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed; on a delete it is flashed on the
nonconformity's page. An action asked for under another nonconformity is a 404.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import CHOOSE_PERSON, AccionCorrectivaForm, VerificacionAccionForm, person_choices
from ..models import AccionCorrectiva, NoConformidad
from ..services import corrective_actions, nonconformities, people
from ..services.actor import Actor
from ..services.errors import Conflict, NotFound, PermissionDenied, ValidationError
from ..utils.permissions import can, require_permission
from ..utils.web_actor import current_actor
from .people_routes import form_data, saved

bp = Blueprint('accion_correctiva', __name__, url_prefix='/no_conformidades')

FORM_FIELDS = ('descripcion', 'responsable_id', 'fecha_prevista', 'fecha_realizada')


def may_verify() -> bool:
    """Whether the logged-in user may verify corrective actions (administrators and auditors)."""
    return (can('update', 'corrective_actions')
            and current_actor().role in corrective_actions.VERIFY_ROLES)


def _nc_page(nc_id: int) -> ResponseReturnValue:
    return redirect(url_for('no_conformidad.ver_no_conformidad', id=nc_id))


def _action(actor: Actor, nc_id: int, accion_id: int) -> AccionCorrectiva:
    """The action, or ``NotFound`` when it belongs to another nonconformity."""
    action = corrective_actions.get(db.session, actor, accion_id)
    if action.no_conformidad_id != nc_id:
        raise NotFound(corrective_actions.NOT_FOUND)
    return action


def _form(actor: Actor, action: AccionCorrectiva | None = None) -> AccionCorrectivaForm:
    """The action form; the owner picker offers active people plus the current owner."""
    form = AccionCorrectivaForm(obj=action)
    owner = action.responsable_id if action is not None else None
    form.responsable_id.choices = person_choices(
        people.choices(db.session, actor, include=owner), empty=CHOOSE_PERSON)
    return form


def _render_form(form: AccionCorrectivaForm, nc: NoConformidad,
                 action: AccionCorrectiva | None = None) -> str:
    return render_template('acciones_correctivas/form.html', form=form, nc=nc, accion=action)


@bp.route('/<int:nc_id>/acciones/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'corrective_actions')
def nueva_accion(nc_id: int) -> ResponseReturnValue:
    actor = current_actor()
    nc = nonconformities.get(db.session, actor, nc_id)
    form = _form(actor)
    if form.validate_on_submit() and saved(lambda: corrective_actions.create(
        db.session, actor, form_data(form, FORM_FIELDS) | {'no_conformidad_id': nc_id}
    )):
        flash('Acción correctiva registrada.', 'success')
        return _nc_page(nc_id)
    return _render_form(form, nc)


@bp.route('/<int:nc_id>/acciones/<int:accion_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'corrective_actions')
def editar_accion(nc_id: int, accion_id: int) -> ResponseReturnValue:
    actor = current_actor()
    nc = nonconformities.get(db.session, actor, nc_id)
    action = _action(actor, nc_id, accion_id)
    form = _form(actor, action)
    if form.validate_on_submit() and saved(lambda: corrective_actions.update(
        db.session, actor, accion_id, form_data(form, FORM_FIELDS)
    )):
        flash('Acción correctiva actualizada.', 'success')
        return _nc_page(nc_id)
    return _render_form(form, nc, action)


@bp.route('/<int:nc_id>/acciones/<int:accion_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'corrective_actions')
def eliminar_accion(nc_id: int, accion_id: int) -> ResponseReturnValue:
    actor = current_actor()
    _action(actor, nc_id, accion_id)
    try:
        corrective_actions.delete(db.session, actor, accion_id)
        db.session.commit()
    except (ValidationError, Conflict) as error:
        db.session.rollback()
        flash(error.message, 'danger')
    else:
        flash('Acción correctiva eliminada.', 'success')
    return _nc_page(nc_id)


@bp.route('/<int:nc_id>/acciones/<int:accion_id>/verificar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'corrective_actions')
def verificar_accion(nc_id: int, accion_id: int) -> ResponseReturnValue:
    if not may_verify():
        raise PermissionDenied()
    actor = current_actor()
    nc = nonconformities.get(db.session, actor, nc_id)
    action = _action(actor, nc_id, accion_id)
    form = VerificacionAccionForm()
    form.verificador_id.choices = person_choices(
        (person for person in people.choices(db.session, actor)
         if person.id != action.responsable_id),
        empty=CHOOSE_PERSON,
    )
    if request.method == 'GET':
        form.fecha_verificacion.data = local_today()
    if form.validate_on_submit() and saved(lambda: corrective_actions.verify(
        db.session, actor, accion_id,
        resultado=form.resultado_verificacion.data,
        fecha=form.fecha_verificacion.data,
        verificador_id=form.verificador_id.data,
        evidencia=form.evidencia_verificacion.data,
    )):
        flash('Verificación registrada.', 'success')
        return _nc_page(nc_id)
    owner = people.names(db.session, actor, [action.responsable_id]).get(action.responsable_id)
    return render_template('acciones_correctivas/verificar.html', form=form, nc=nc,
                           accion=action, responsable=owner)
