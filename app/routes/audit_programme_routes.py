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

"""Audit programme screens (ISO 9001 clause 9.2.2): adapters over ``audit_programmes``.

Task AP-3 of ``audit-programme``. Programmes share the ``AUDITS`` grant:
administrators and auditors list, read, create and edit them; administrators
approve (``may_approve``, decision AP1) and delete. The approver picker offers
active people and starts on the person linked to the user; the approval date
is ``local_today``. A programme's page lists its audits with their findings
counted by type.

Programmes are one per year, so the list shows them all; a programme's page
shows its first ``crud.MAX_PER_PAGE`` audits and says so when there are more.
Its PDF (``/<id>/pdf``, task AP-4) has the same access as the page and lists
every audit by date, with the year, state, approval, objectives and scope.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed; a refused delete, or the approval
page of a programme that is no longer a draft, returns to the programme's page
with the reason.
"""

from collections import Counter

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import CHOOSE_PERSON, AprobarProgramaForm, ProgramaAuditoriaForm, person_choices
from ..models import Auditoria, EstadoProgramaAuditoria, ProgramaAuditoria, TipoHallazgo
from ..services import audit_findings, audit_programmes, audits, crud, people
from ..services.actor import Actor
from ..services.errors import PermissionDenied
from ..utils.pdf import pdf_response, render_pdf
from ..utils.permissions import can, require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

bp = Blueprint('programa_auditoria', __name__, url_prefix='/programas-auditoria')

FORM_FIELDS = ('anio', 'objetivos', 'alcance')


def may_approve() -> bool:
    """Whether the logged-in user may approve programmes (administrators, AP1)."""
    return (can('update', 'audits')
            and current_actor().role in audit_programmes.APPROVE_ROLES)


def _programme_page(programme_id: int) -> ResponseReturnValue:
    return redirect(url_for('programa_auditoria.ver_programa', id=programme_id))


def _audit_count(actor: Actor, programme_id: int) -> int:
    return audits.list_page(db.session, actor, programa_id=programme_id, per_page=1)[1]


def _every_audit(actor: Actor, programme_id: int) -> list[Auditoria]:
    """All the programme's audits, by date, read one bounded page at a time."""
    found: list[Auditoria] = []
    page = 1
    while True:
        batch, total = audits.list_page(db.session, actor, programa_id=programme_id,
                                        page=page, per_page=crud.MAX_PER_PAGE)
        found += batch
        if not batch or len(found) >= total or page >= crud.MAX_PAGE:
            return sorted(found, key=lambda a: (a.fecha, a.id))
        page += 1


def _audits_context(actor: Actor, programa: ProgramaAuditoria,
                    auditorias: list[Auditoria]) -> dict:
    """The programme's audits with their findings counted by type, and the names cited."""
    return dict(
        programa=programa,
        auditorias=auditorias,
        hallazgos={
            a.id: Counter(f.tipo for f in audit_findings.list_(db.session, actor,
                                                               auditoria_id=a.id))
            for a in auditorias
        },
        tipos=list(TipoHallazgo),
        personas=people.names(db.session, actor, (
            programa.aprobado_por_id, *(a.auditor_id for a in auditorias))),
    )


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'audits')
def listar_programas() -> ResponseReturnValue:
    """Every programme, newest year first, with how many audits each holds."""
    actor = current_actor()
    programas = audit_programmes.list_(db.session, actor)
    return render_template(
        'programas_auditoria/listar.html',
        programas=programas,
        auditorias={p.id: _audit_count(actor, p.id) for p in programas},
        personas=people.names(db.session, actor, (p.aprobado_por_id for p in programas)),
    )


@bp.route('/<int:id>', methods=['GET'])
@login_required
@require_permission('read', 'audits')
def ver_programa(id: int) -> ResponseReturnValue:
    """A programme's page: its data, its approval and its audits with their findings."""
    actor = current_actor()
    programa = audit_programmes.get(db.session, actor, id)
    auditorias, total = audits.list_page(db.session, actor, programa_id=id,
                                         per_page=crud.MAX_PER_PAGE)
    return render_template(
        'programas_auditoria/detalle.html',
        **_audits_context(actor, programa, auditorias),
        total_auditorias=total,
        puede_aprobar=may_approve() and programa.estado is EstadoProgramaAuditoria.borrador,
    )


@bp.route('/<int:id>/pdf', methods=['GET'])
@login_required
@require_permission('read', 'audits')
def exportar_pdf(id: int) -> ResponseReturnValue:
    """The programme as a PDF: its data, its approval and every audit with its findings."""
    actor = current_actor()
    programa = audit_programmes.get(db.session, actor, id)
    try:
        pdf = render_pdf('programas_auditoria/pdf_template.html',
                         **_audits_context(actor, programa, _every_audit(actor, id)))
    except Exception:
        current_app.logger.exception('Error generating PDF for audit programme %s', id)
        flash('Error al generar el PDF del programa de auditoría.', 'danger')
        return redirect(url_for('programa_auditoria.ver_programa', id=id))
    return pdf_response(pdf, f'programa_auditoria_{programa.anio}.pdf')


@bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'audits')
def nuevo_programa() -> ResponseReturnValue:
    """Create a draft programme and open its page."""
    actor = current_actor()
    form = ProgramaAuditoriaForm()
    if form.validate_on_submit() and (created := saved(lambda: audit_programmes.create(
        db.session, actor, form_data(form, FORM_FIELDS)
    ))):
        flash(f'Programa de auditoría {created.anio} creado.', 'success')
        return _programme_page(created.id)
    return render_template('programas_auditoria/form.html', form=form, programa=None)


@bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'audits')
def editar_programa(id: int) -> ResponseReturnValue:
    """Edit year, objectives and scope; the service keeps an approved programme's year."""
    actor = current_actor()
    programa = audit_programmes.get(db.session, actor, id)
    form = ProgramaAuditoriaForm(obj=programa)
    if form.validate_on_submit() and committed(lambda: audit_programmes.update(
        db.session, actor, id, form_data(form, FORM_FIELDS)
    )):
        flash('Programa de auditoría actualizado.', 'success')
        return _programme_page(id)
    return render_template('programas_auditoria/form.html', form=form, programa=programa)


@bp.route('/<int:id>/aprobar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'audits')
def aprobar_programa(id: int) -> ResponseReturnValue:
    """Approve a draft programme today; administrators only (AP1)."""
    if not may_approve():
        raise PermissionDenied()
    actor = current_actor()
    programa = audit_programmes.get(db.session, actor, id)
    if programa.estado is not EstadoProgramaAuditoria.borrador:
        flash(audit_programmes.NOT_DRAFT, 'danger')
        return _programme_page(id)
    form = AprobarProgramaForm()
    form.aprobado_por_id.choices = person_choices(people.choices(db.session, actor),
                                                  empty=CHOOSE_PERSON)
    if request.method == 'GET':
        form.aprobado_por_id.data = people.of_user(db.session, actor.user_id)
    if form.validate_on_submit() and committed(lambda: audit_programmes.approve(
        db.session, actor, id, aprobado_por_id=form.aprobado_por_id.data, today=local_today()
    )):
        flash(f'Programa de auditoría {programa.anio} aprobado.', 'success')
        return _programme_page(id)
    return render_template('programas_auditoria/aprobar.html', form=form, programa=programa)


@bp.route('/<int:id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'audits')
def eliminar_programa(id: int) -> ResponseReturnValue:
    """Delete a programme without audits (administrators)."""
    if committed(lambda: audit_programmes.delete(db.session, current_actor(), id)):
        flash('Programa de auditoría eliminado.', 'success')
        return redirect(url_for('programa_auditoria.listar_programas'))
    return _programme_page(id)
