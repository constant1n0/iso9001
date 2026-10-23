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

"""QMS scope screens (ISO 9001 clause 4.3): adapters over ``qms_scope``.

Task OC-3 of ``organization-context``, under the policy resource
``organization_context``: every role reads every version; administrators and
auditors start, edit and delete drafts; administrators approve (decisions OC3
and OC4). The scope lives under ``/alcance`` (blueprint ``alcance``):

- The current version, or a note that there is none yet, with the draft linked;
  the history; and each version's page with the applicability table and the
  draft actions.
- "Nuevo borrador" takes the texts (the current version's on first view) and
  copies the current version's applicability; there is at most one draft, so
  asking for another goes to it.
- The draft form edits the texts and one row per catalogue requirement
  ("aplica" and its justification); a requirement that does not apply without a
  reason is refused on its row (``qms_scope.applicability_errors``) and nothing
  is written.
- Approving is for administrators (``qms_scope.may_approve``); deleting a draft
  is for its writers. Current and obsolete versions never change: their forms
  return to the version's page with the reason.

A service ``ValidationError`` or ``Conflict`` is rolled back: on a form it
shows again with what was typed and the reason flashed, on a one-button action
it is flashed on the version's page. Dates come from ``local_today``.
"""

from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import AlcanceForm
from ..models import AlcanceSGC, EstadoAlcance
from ..services import people, qms_scope
from ..services.actor import Actor
from ..services.errors import PermissionDenied
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

scope_bp = Blueprint('alcance', __name__, url_prefix='/alcance')

RESOURCE = 'organization_context'


def _version_page(scope_id: int) -> ResponseReturnValue:
    return redirect(url_for('alcance.show_version', scope_id=scope_id))


def _version(actor: Actor, alcance: AlcanceSGC, **context: Any) -> str:
    """A version's page: its data, the applicability table and the draft actions."""
    return render_template(
        'alcance/version.html', alcance=alcance,
        requisitos=qms_scope.applicability(db.session, actor, alcance.id),
        personas=people.names(db.session, actor, (alcance.aprobado_por_id,)),
        aprobable=alcance.estado is EstadoAlcance.borrador and qms_scope.may_approve(actor),
        **context)


@scope_bp.route('/', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def show_current() -> ResponseReturnValue:
    """The version in force, or a note that there is none; the draft is linked."""
    actor = current_actor()
    vigente = qms_scope.current(db.session, actor)
    borrador = qms_scope.draft(db.session, actor)
    if vigente is None:
        return render_template('alcance/none.html', borrador=borrador)
    return _version(actor, vigente, actual=True, borrador=borrador)


@scope_bp.route('/historial', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def history() -> ResponseReturnValue:
    """Every version, the newest first."""
    actor = current_actor()
    versiones = qms_scope.history(db.session, actor)
    return render_template('alcance/history.html', versiones=versiones,
                           personas=people.names(db.session, actor, (
                               version.aprobado_por_id for version in versiones)))


@scope_bp.route('/<int:scope_id>', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def show_version(scope_id: int) -> ResponseReturnValue:
    actor = current_actor()
    return _version(actor, qms_scope.get(db.session, actor, scope_id))


@scope_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_draft() -> ResponseReturnValue:
    """Start a draft from the given texts, copying the current version's applicability."""
    actor = current_actor()
    borrador = qms_scope.draft(db.session, actor)
    if borrador is not None:
        flash(qms_scope.ONE_DRAFT, 'warning')
        return _version_page(borrador.id)
    vigente = qms_scope.current(db.session, actor)
    form = AlcanceForm(obj=vigente)
    if form.validate_on_submit() and (created := saved(lambda: qms_scope.new_draft(
            db.session, actor, form_data(form, qms_scope.TEXT_FIELDS)))):
        flash(f'Borrador del alcance creado (versión {created.version}).', 'success')
        return _version_page(created.id)
    return render_template('alcance/new.html', form=form, vigente=vigente)


def _posted_rows() -> dict[str, dict[str, Any]]:
    """The applicability the draft form posts: one row per catalogue requirement."""
    return {code: {'aplica': f'aplica-{code}' in request.form,
                   'justificacion': request.form.get(f'justificacion-{code}', '')}
            for code, _ in qms_scope.catalogue()}


@scope_bp.route('/<int:scope_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_draft(scope_id: int) -> ResponseReturnValue:
    """Edit a draft's texts and applicability in one form; other versions never change."""
    actor = current_actor()
    alcance = qms_scope.get(db.session, actor, scope_id)
    if alcance.estado is not EstadoAlcance.borrador:
        flash(qms_scope.IMMUTABLE, 'danger')
        return _version_page(scope_id)
    form = AlcanceForm(obj=alcance)
    catalogue = dict(qms_scope.catalogue())
    stored = [r for r in qms_scope.applicability(db.session, actor, scope_id)
              if r.apartado in catalogue]  # a code outside the catalogue is left as it is
    filas = {r.apartado: {'aplica': r.aplica, 'justificacion': r.justificacion or ''}
             for r in stored}
    errores: dict[str, str] = {}
    if request.method == 'POST':
        filas = _posted_rows()
        errores = qms_scope.applicability_errors(filas)

    def write() -> AlcanceSGC:
        data = form_data(form, qms_scope.TEXT_FIELDS) | {'aplicabilidad': filas}
        return qms_scope.update_draft(db.session, actor, scope_id, data)

    if form.validate_on_submit() and not errores and saved(write):
        flash('Borrador del alcance actualizado.', 'success')
        return _version_page(scope_id)
    return render_template('alcance/form.html', form=form, alcance=alcance,
                           requisitos=stored, filas=filas, errores=errores)


@scope_bp.route('/<int:scope_id>/eliminar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def delete_draft(scope_id: int) -> ResponseReturnValue:
    """Delete a draft with its applicability (its writers: administrators and auditors)."""
    if committed(lambda: qms_scope.delete_draft(db.session, current_actor(), scope_id)):
        flash('Borrador del alcance eliminado.', 'success')
        return redirect(url_for('alcance.show_current'))
    return _version_page(scope_id)


@scope_bp.route('/<int:scope_id>/aprobar', methods=['POST'])
@login_required
@require_permission('update', RESOURCE)
def approve(scope_id: int) -> ResponseReturnValue:
    """Put a draft in force today; the current version becomes obsolete (administrators)."""
    actor = current_actor()
    if not qms_scope.may_approve(actor):
        raise PermissionDenied()
    if committed(lambda: qms_scope.approve(db.session, actor, scope_id, today=local_today())):
        version = qms_scope.get(db.session, actor, scope_id).version
        flash(f'Versión {version} del alcance aprobada; está vigente desde hoy.', 'success')
        return redirect(url_for('alcance.show_current'))
    return _version_page(scope_id)
