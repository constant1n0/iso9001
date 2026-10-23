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

"""Context issue screens (ISO 9001 clause 4.1): adapters over ``context_issues``.

Task OC-3 of ``organization-context``, under the policy resource
``organization_context``: every role reads; administrators and auditors create
and edit issues; administrators delete them (decisions OC1 and OC4). The QMS
scope (clause 4.3) has its own screens under the same resource.

Context issues live under ``/contexto`` (blueprint ``contexto``):

- The list, by type, category and title, filters by type, category, relevance,
  climate change, state, owner and overdue review (what the dashboard links); a
  value that names nothing is ignored, so it neither filters nor counts. Each
  issue shows its next review with an overdue badge and how many risks and
  opportunities it gives rise to.
- An issue's page shows every field and links its risks and opportunities, and
  the risk and opportunity lists filtered by the issue.
- The form offers both groups of categories whatever the type: the service
  decides, and its refusals (category, justification, dates) show on their
  field with the input kept. A cited issue offers no delete; the service
  refuses it anyway and the reason is flashed on its page.

A service ``ValidationError`` or ``Conflict`` is rolled back: on a form it
shows again with what was typed, on a one-button action it is flashed on the
record's page. Dates come from ``local_today``.
"""

from collections import Counter
from collections.abc import Callable
from typing import Any, TypeVar

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import CuestionContextoForm, person_choices
from ..models import CategoriaCuestion, CuestionContexto, EstadoCuestion, TipoContexto
from ..services import context_issues as ci
from ..services import people, risks
from ..services.actor import Actor
from ..services.errors import Conflict, ValidationError
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data

issues_bp = Blueprint('contexto', __name__, url_prefix='/contexto')

RESOURCE = 'organization_context'
T = TypeVar('T')
ISSUE_FIELDS = ('tipo', 'categoria', 'titulo', 'descripcion', 'efecto', 'pertinente',
                'justificacion_pertinencia', 'cambio_climatico', 'responsable_id',
                'fecha_identificacion', 'fecha_revision', 'estado', 'notas')
FLAGS = {'1': True, '0': False}
YES_NO = [('1', 'Sí'), ('0', 'No')]
# The service refusals shown on their field instead of flashed.
ISSUE_FIELD_ERRORS = {
    ci.CATEGORY_MISMATCH: 'categoria',
    ci.JUSTIFICATION_REQUIRED: 'justificacion_pertinencia',
    ci.IDENTIFIED_IN_FUTURE: 'fecha_identificacion',
    ci.REVIEW_BEFORE_IDENTIFICATION: 'fecha_revision',
}


def _members(enum_cls: type) -> list[tuple[str, str]]:
    return [(member.name, member.value) for member in enum_cls]


def _written(form: Any, write: Callable[[], T], on_field: dict[str, str]) -> T | None:
    """Run a service write and commit it; a refusal is rolled back and shown.

    A refusal ``on_field`` names goes on that field; any other is flashed.
    Returns the written record, or ``None`` after a refusal.
    """
    try:
        written = write()
        db.session.commit()
    except (ValidationError, Conflict) as error:
        db.session.rollback()
        if (name := on_field.get(error.message)) is not None:
            getattr(form, name).errors.append(error.message)
        else:
            flash(error.message, 'danger')
        return None
    return written


# -- context issues --------------------------------------------------------------------


def _issue_page(issue_id: int) -> ResponseReturnValue:
    return redirect(url_for('contexto.show_issue', issue_id=issue_id))


@issues_bp.route('/', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def list_issues() -> ResponseReturnValue:
    """Issues matching the filters; ``applied`` counts the filters used."""
    actor = current_actor()
    today = local_today()
    every = ci.issues.list_(db.session, actor)
    owners = people.names(db.session, actor, (issue.responsable_id for issue in every))
    fields: list[tuple[str, str, list[tuple[str, str]], Callable[[str], Any]]] = [
        ('tipo', 'Tipo', _members(TipoContexto), str),
        ('categoria', 'Categoría', _members(CategoriaCuestion), str),
        ('pertinente', 'Pertinente', YES_NO, FLAGS.get),
        ('cambio_climatico', 'Cambio climático', YES_NO, FLAGS.get),
        ('estado', 'Estado', _members(EstadoCuestion), str),
        ('responsable_id', 'Responsable',
         [(str(person_id), nombre) for person_id, nombre
          in sorted(owners.items(), key=lambda item: item[1].casefold())], int),
        ('revision_vencida', 'Revisión vencida', YES_NO, FLAGS.get),
    ]
    chosen, selected = {}, {}
    for name, _, choices, value_of in fields:
        text = request.args.get(name, '')
        if text in {value for value, _ in choices}:
            selected[name], chosen[name] = text, value_of(text)
    found = ci.issues.list_(db.session, actor, today=today, **chosen) if chosen else every
    return render_template(
        'contexto/list.html', cuestiones=found, personas=owners,
        vencidas={issue.id for issue in found if ci.revision_vencida(issue, today=today)},
        riesgos=_citations(risks.risks.list_(db.session, actor)),
        oportunidades=_citations(risks.opportunities.list_(db.session, actor)),
        campos=fields, elegidos=selected, applied=len(chosen))


def _citations(records: list[Any]) -> Counter:
    """How many of ``records`` (risks or opportunities) name each issue."""
    return Counter(record.cuestion_id for record in records if record.cuestion_id is not None)


@issues_bp.route('/<int:issue_id>', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def show_issue(issue_id: int) -> ResponseReturnValue:
    """An issue's page: its fields and the risks and opportunities it gives rise to."""
    actor = current_actor()
    cuestion = ci.issues.get(db.session, actor, issue_id)
    vinculos = ci.linked(db.session, actor, issue_id)
    return render_template(
        'contexto/detail.html', cuestion=cuestion, vinculos=vinculos,
        vencida=ci.revision_vencida(cuestion, today=local_today()),
        personas=people.names(db.session, actor, (cuestion.responsable_id,)),
        eliminable=not vinculos['riesgos'] and not vinculos['oportunidades'])


def _issue_form(actor: Actor, cuestion: CuestionContexto | None = None) -> CuestionContextoForm:
    """The issue form; the owner picker offers active people and the stored one."""
    form = CuestionContextoForm(obj=cuestion)
    form.responsable_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(cuestion, 'responsable_id', None)))
    return form


@issues_bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def new_issue() -> ResponseReturnValue:
    """Register an issue; its identification date starts on today."""
    actor = current_actor()
    form = _issue_form(actor)
    if request.method == 'GET':
        form.fecha_identificacion.data = local_today()
    if form.validate_on_submit() and (created := _written(form, lambda: ci.issues.create(
            db.session, actor, form_data(form, ISSUE_FIELDS), today=local_today()),
            ISSUE_FIELD_ERRORS)):
        flash(f'Cuestión «{created.titulo}» creada.', 'success')
        return _issue_page(created.id)
    return render_template('contexto/form.html', form=form, cuestion=None)


@issues_bp.route('/<int:issue_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def edit_issue(issue_id: int) -> ResponseReturnValue:
    actor = current_actor()
    cuestion = ci.issues.get(db.session, actor, issue_id)
    form = _issue_form(actor, cuestion)
    if form.validate_on_submit() and _written(form, lambda: ci.issues.update(
            db.session, actor, issue_id, form_data(form, ISSUE_FIELDS), today=local_today()),
            ISSUE_FIELD_ERRORS):
        flash('Cuestión actualizada.', 'success')
        return _issue_page(issue_id)
    return render_template('contexto/form.html', form=form, cuestion=cuestion)


@issues_bp.route('/<int:issue_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', RESOURCE)
def delete_issue(issue_id: int) -> ResponseReturnValue:
    """Delete an issue no risk or opportunity names (administrators)."""
    if committed(lambda: ci.issues.delete(db.session, current_actor(), issue_id)):
        flash('Cuestión eliminada.', 'success')
        return redirect(url_for('contexto.list_issues'))
    return _issue_page(issue_id)
