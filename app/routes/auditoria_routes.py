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

"""Audit screens (ISO 9001 clause 9.2): adapters over ``audits`` and ``audit_findings``.

Policy ``AUDITS``: administrators and auditors read and write audits and their
findings, administrators delete; ``OPERATIVO`` sees nothing. An audit's page
shows its planning fields (task AP-3 of ``audit-programme``) and its findings,
each nonconformity finding linked to the nonconformity it opened. The finding
forms live under their audit (``/auditorias/<id>/hallazgos/…``); a finding asked
for under another audit is a 404, and a cancelled audit shows its findings
read-only (the service refuses writes to them too).

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed; on a delete it is flashed on the
audit's page.

The audit's PDF (task AP-4) holds the legacy fields, the planning fields and a
table of the findings with their nonconformity numbers.
"""

from collections.abc import Callable
from math import ceil

from flask import Blueprint, current_app, render_template, redirect, url_for, flash, request
from flask.typing import ResponseReturnValue
from flask_login import login_required
from ..models import Auditoria, EstadoAuditoriaEnum, HallazgoAuditoria
from ..forms import AuditoriaForm, HallazgoForm, person_choices, programme_choices
from ..extensions import db
from ..services import audit_findings, audit_programmes, audits, people
from ..services.actor import Actor
from ..services.errors import Conflict, NotFound, ValidationError
from ..utils.permissions import require_permission
from ..utils.pdf import pdf_response, render_pdf
from ..utils.web_actor import current_actor
from ..utils.web_args import date_arg
from .people_routes import form_data, saved

bp = Blueprint('auditoria', __name__, url_prefix='/auditorias')

FORM_FIELDS = ('area_auditada', 'fecha', 'auditor', 'auditor_id', 'resultado',
               'accion_correctiva', 'estado', 'programa_id', 'alcance', 'criterios',
               'responsable_area_id', 'equipo_ids')
FINDING_FIELDS = ('tipo', 'clausula', 'descripcion', 'evidencia')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


def _with_choices(form, actor, auditoria=None):
    """Fill the programme and people pickers; people the audit cites stay offered."""
    form.programa_id.choices = programme_choices(audit_programmes.list_(db.session, actor))
    for name in ('auditor_id', 'responsable_area_id'):
        getattr(form, name).choices = person_choices(
            people.choices(db.session, actor, include=getattr(auditoria, name, None)))
    team = people.choices(db.session, actor) + [
        p for p in getattr(auditoria, 'equipo', []) if not p.activo]
    form.equipo_ids.choices = person_choices(team)[1:]  # no empty choice in a multi-select
    return form


def committed(write: Callable[[], object]) -> bool:
    """Run a service write and commit it; on a refusal roll back, flash why, return False.

    A form route then shows the form again, keeping what the user typed.
    """
    try:
        write()
        db.session.commit()
    except (ValidationError, Conflict) as error:
        db.session.rollback()
        flash(error.message, 'danger')
        return False
    return True


def _audit_page(audit_id: int) -> ResponseReturnValue:
    return redirect(url_for('auditoria.ver_auditoria', id=audit_id))


def _estado_arg():
    """Query-string state as an enum member, or None when absent or unknown."""
    try:
        return EstadoAuditoriaEnum(request.args.get('estado'))
    except ValueError:
        return None


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'audits')
def listar_auditorias():
    """
    Lista todas las auditorías registradas en el sistema, con funcionalidad de búsqueda y filtrado avanzado.
    Incluye paginación para manejar grandes volúmenes de datos.
    """
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Número de auditorías por página

    actor = current_actor()
    auditorias, total_auditorias = audits.list_page(
        db.session,
        actor,
        area=request.args.get('area'),
        auditor=request.args.get('auditor'),
        estado=_estado_arg(),
        fecha_inicio=date_arg('fecha_inicio'),
        fecha_fin=date_arg('fecha_fin'),
        page=page,
        per_page=per_page,
    )

    return render_template(
        'auditorias/listar.html', 
        auditorias=auditorias, 
        page=page, 
        total_pages=ceil(total_auditorias / per_page),
        total_auditorias=total_auditorias,
        personas=people.names(db.session, actor, (a.auditor_id for a in auditorias)),
    )

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'audits')
def nueva_auditoria():
    """
    Muestra el formulario para crear una nueva auditoría y guarda el registro
    en la base de datos al enviarlo.
    """
    actor = current_actor()
    form = _with_choices(AuditoriaForm(), actor)
    if request.method == 'GET':  # «+ Nueva auditoría» on a programme's page
        form.programa_id.data = request.args.get('programa_id', type=int)
    if form.validate_on_submit() and committed(
        lambda: audits.create(db.session, actor, _form_data(form))
    ):
        flash('Auditoría creada exitosamente', 'success')
        return redirect(url_for('auditoria.listar_auditorias'))
    return render_template('auditorias/nueva.html', form=form)

@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'audits')
def editar_auditoria(id):
    """
    Carga el formulario de edición de una auditoría existente y guarda los
    cambios realizados en la base de datos.
    """
    actor = current_actor()
    auditoria = audits.get(db.session, actor, id)
    form = AuditoriaForm(obj=auditoria)

    # Establecer el valor actual del estado en el formulario
    if request.method == 'GET' and auditoria.estado:
        form.estado.data = auditoria.estado.name

    _with_choices(form, actor, auditoria)
    if form.validate_on_submit() and committed(
        lambda: audits.update(db.session, actor, id, _form_data(form))
    ):
        flash('Auditoría actualizada exitosamente', 'success')
        return redirect(url_for('auditoria.listar_auditorias'))
    return render_template('auditorias/editar.html', form=form, auditoria=auditoria)

@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@require_permission('delete', 'audits')
def eliminar_auditoria(id):
    """
    Elimina una auditoría existente de la base de datos; si se rechaza (por
    ejemplo, porque tiene hallazgos), vuelve a su página con el motivo.
    """
    if committed(lambda: audits.delete(db.session, current_actor(), id)):
        flash('Auditoría eliminada exitosamente', 'success')
        return redirect(url_for('auditoria.listar_auditorias'))
    return _audit_page(id)


def _audit_context(actor: Actor, auditoria: Auditoria) -> dict:
    """What the audit's page and PDF show next to the audit: programme, findings, names."""
    programa = (audit_programmes.get(db.session, actor, auditoria.programa_id)
                if auditoria.programa_id is not None else None)
    return dict(
        auditoria=auditoria,
        programa=programa,
        hallazgos=audit_findings.list_(db.session, actor, auditoria_id=auditoria.id),
        personas=people.names(db.session, actor,
                              (auditoria.auditor_id, auditoria.responsable_area_id)),
    )


@bp.route('/<int:id>', methods=['GET'])
@login_required
@require_permission('read', 'audits')
def ver_auditoria(id: int) -> ResponseReturnValue:
    """An audit's page: its planning fields, its findings and the changes it gives rise to."""
    from .change_routes import citing_changes  # here: change_routes imports this module

    actor = current_actor()
    auditoria = audits.get(db.session, actor, id)
    return render_template(
        'auditorias/detalle.html',
        **_audit_context(actor, auditoria),
        cancelada=auditoria.estado is EstadoAuditoriaEnum.CANCELADA,
        cambios=citing_changes(actor, 'auditoria', id),
    )


def _finding(actor: Actor, audit_id: int, finding_id: int) -> HallazgoAuditoria:
    """The finding, or ``NotFound`` when it belongs to another audit."""
    found = audit_findings.get(db.session, actor, finding_id)
    if found.auditoria_id != audit_id:
        raise NotFound(audit_findings.NOT_FOUND)
    return found


def _cancelled(auditoria: Auditoria) -> bool:
    """Flash why a cancelled audit's findings are read-only, and say whether it is."""
    if auditoria.estado is EstadoAuditoriaEnum.CANCELADA:
        flash(audit_findings.AUDIT_CANCELLED, 'danger')
        return True
    return False


def _saved_finding(verb: str, finding: HallazgoAuditoria, linked_before: int | None) -> None:
    """Flash a saved finding, naming the nonconformity it has just opened."""
    message = f'Hallazgo {verb}.'
    if finding.no_conformidad_id not in (None, linked_before):
        message += f' Se ha abierto la no conformidad n.º {finding.no_conformidad_id}.'
    flash(message, 'success')


def _finding_form(form: HallazgoForm, auditoria: Auditoria,
                  hallazgo: HallazgoAuditoria | None = None) -> str:
    return render_template('auditorias/hallazgo_form.html', form=form, auditoria=auditoria,
                           hallazgo=hallazgo)


@bp.route('/<int:id>/hallazgos/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'audits')
def nuevo_hallazgo(id: int) -> ResponseReturnValue:
    """Record a finding; a nonconformity finding opens its nonconformity (AP5)."""
    actor = current_actor()
    auditoria = audits.get(db.session, actor, id)
    if _cancelled(auditoria):
        return _audit_page(id)
    form = HallazgoForm()
    if form.validate_on_submit() and (created := saved(lambda: audit_findings.create(
        db.session, actor, form_data(form, FINDING_FIELDS) | {'auditoria_id': id}
    ))):
        _saved_finding('registrado', created, None)
        return _audit_page(id)
    return _finding_form(form, auditoria)


@bp.route('/<int:id>/hallazgos/<int:hallazgo_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'audits')
def editar_hallazgo(id: int, hallazgo_id: int) -> ResponseReturnValue:
    """Edit a finding; its nonconformity follows the rules of ``audit_findings``."""
    actor = current_actor()
    auditoria = audits.get(db.session, actor, id)
    hallazgo = _finding(actor, id, hallazgo_id)
    if _cancelled(auditoria):
        return _audit_page(id)
    linked_before = hallazgo.no_conformidad_id
    form = HallazgoForm(obj=hallazgo)
    if form.validate_on_submit() and (updated := saved(lambda: audit_findings.update(
        db.session, actor, hallazgo_id, form_data(form, FINDING_FIELDS)
    ))):
        _saved_finding('actualizado', updated, linked_before)
        return _audit_page(id)
    return _finding_form(form, auditoria, hallazgo)


@bp.route('/<int:id>/hallazgos/<int:hallazgo_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'audits')
def eliminar_hallazgo(id: int, hallazgo_id: int) -> ResponseReturnValue:
    """Delete a finding (administrators); its nonconformity stays."""
    actor = current_actor()
    auditoria = audits.get(db.session, actor, id)
    _finding(actor, id, hallazgo_id)
    if not _cancelled(auditoria) and committed(
        lambda: audit_findings.delete(db.session, actor, hallazgo_id)
    ):
        flash('Hallazgo eliminado.', 'success')
    return _audit_page(id)

@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
@require_permission('read', 'audits')
def exportar_pdf(id):
    """
    Genera el PDF de una auditoría: sus campos, su planificación y sus hallazgos.
    """
    actor = current_actor()
    context = _audit_context(actor, audits.get(db.session, actor, id))

    try:
        return pdf_response(render_pdf('auditorias/pdf_template.html', **context),
                            f'auditoria_{id}.pdf')
    except Exception:
        current_app.logger.exception('Error generating PDF for audit %s', id)
        flash('Error al generar el PDF de la auditoría.', 'danger')
        return redirect(url_for('auditoria.listar_auditorias'))
