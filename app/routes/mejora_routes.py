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

"""Improvement register HTML, JSON and web-only lifecycle adapters."""

from collections.abc import Mapping
from datetime import date

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from ..audit_notifications import local_today
from ..forms import (
    MejoraForm, ImplantacionMejoraForm, EficaciaMejoraForm, CancelacionMejoraForm,
    person_choices,
)
from ..models import MEJORA_LINKS, EstadoMejora, OrigenMejora
from ..schemas import MejoraSchema
from ..extensions import db
from ..services import (
    audit_findings, crud, improvements, management_reviews, nonconformities,
    people, quality_objectives, risks,
)
from ..utils.permissions import can, require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .change_routes import _valid, _written
from .json_register import register_json_api

bp = Blueprint('mejora', __name__, url_prefix='/mejoras')
FORM_FIELDS = tuple(f.name for f in improvements.SPEC.fields)


class _Pagination:
    """The fields the existing register pagination reads."""
    def __init__(self, page, per_page, total):
        self.page = page
        self.pages = -(-total // per_page)
        self.has_prev = page > 1
        self.prev_num = page - 1 if self.has_prev else None
        self.has_next = page < self.pages
        self.next_num = page + 1 if self.has_next else None


def _form(actor, mejora=None):
    form = MejoraForm(obj=mejora)
    form.responsable_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(mejora, 'responsable_id', None)))
    offered = {
        'no_conformidad_id': ('nonconformities', nonconformities.list_, 'descripcion'),
        'hallazgo_id': ('audits', audit_findings.list_, 'descripcion'),
        'accion_revision_id': ('management_review', management_reviews.actions.list_, 'descripcion'),
        'oportunidad_id': ('risks_opportunities', risks.opportunities.list_, 'titulo'),
        'objetivo_id': ('quality_objectives', quality_objectives.objectives.list_, 'titulo'),
    }
    for column, (resource, listing, label) in offered.items():
        records = listing(db.session, actor) if can('read', resource) else []
        choices = [('', '— Sin registro vinculado —'),
                   *((record.id, getattr(record, label)[:90]) for record in records)]
        current = getattr(mejora, column, None)
        if current is not None and current not in {record.id for record in records}:
            choices.append((current, 'Registro vinculado'))
        getattr(form, column).choices = choices
    if mejora is None and request.method == 'GET':
        form.fecha_identificacion.data = local_today()
    return form


def _form_data(form):
    data = {name: getattr(form, name).data for name in FORM_FIELDS}
    # Changing origin clears the old link; the contextual form only posts the current one.
    for column, (origin, _) in MEJORA_LINKS.items():
        if form.origen.data != origin.name:
            data[column] = None
    return data


def _page(record_id):
    return redirect(url_for('mejora.ver_mejora', id=record_id))


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'improvements')
def listar_mejoras():
    page, per_page = crud.page_bounds(request.args.get('page', 1, type=int),
                                      request.args.get('per_page', 10, type=int))
    filters = {}
    for key, members in (('estado', EstadoMejora), ('origen', OrigenMejora)):
        if request.args.get(key) in members.__members__:
            filters[key] = request.args[key]
    if (owner := request.args.get('responsable_id', type=int)) is not None:
        filters['responsable_id'] = owner
    actor = current_actor()
    mejoras, total = improvements.list_page(db.session, actor, page=page, per_page=per_page, **filters)
    return render_template(
        'mejoras/listar.html', mejoras=mejoras, pagination=_Pagination(page, per_page, total),
        personas=people.names(db.session, actor, (m.responsable_id for m in mejoras)),
        estados=EstadoMejora, origenes=OrigenMejora, filtros=filters, per_page=per_page)


@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'improvements')
def nueva_mejora():
    actor = current_actor()
    form = _form(actor)
    if 'elegir_origen' not in request.form and form.validate_on_submit() and _written(
            form, lambda: improvements.create(db.session, actor, _form_data(form), today=local_today()),
            {improvements.IDENTIFIED_IN_FUTURE: 'fecha_identificacion'}):
        flash('Mejora registrada exitosamente', 'success')
        return redirect(url_for('mejora.listar_mejoras'))
    return render_template('mejoras/nueva.html', form=form, links=MEJORA_LINKS)


@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'improvements')
def editar_mejora(id):
    actor = current_actor()
    mejora = improvements.get(db.session, actor, id)
    form = _form(actor, mejora)
    if 'elegir_origen' not in request.form and form.validate_on_submit() and _written(
            form, lambda: improvements.update(db.session, actor, id, _form_data(form), today=local_today()),
            {improvements.IDENTIFIED_IN_FUTURE: 'fecha_identificacion'}):
        flash('Mejora actualizada exitosamente', 'success')
        return redirect(url_for('mejora.listar_mejoras'))
    return render_template('mejoras/editar.html', form=form, mejora=mejora, links=MEJORA_LINKS)


@bp.route('/<int:id>', methods=['GET'])
@login_required
@require_permission('read', 'improvements')
def ver_mejora(id):
    actor = current_actor()
    mejora = improvements.get(db.session, actor, id)
    return render_template(
        'mejoras/detalle.html', mejora=mejora,
        personas=people.names(db.session, actor, (mejora.responsable_id, mejora.aprobado_por_id,
                                                  mejora.revisor_eficacia_id)),
        links=MEJORA_LINKS,
        puede={name: guard(actor, mejora) for name, guard in (
            ('aprobar', improvements.may_approve), ('implantar', improvements.may_implement),
            ('eficacia', improvements.may_review_effectiveness), ('cancelar', improvements.may_cancel))},
        implantacion=ImplantacionMejoraForm(fecha_implementacion=local_today()),
        eficacia=EficaciaMejoraForm(fecha_eficacia=local_today()), cancelacion=CancelacionMejoraForm())


@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_mejora(id):
    improvements.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('Mejora eliminada correctamente', 'success')
    return redirect(url_for('mejora.listar_mejoras'))


@bp.route('/<int:id>/aprobar', methods=['POST'])
@login_required
@require_permission('update', 'improvements')
def aprobar_mejora(id):
    if committed(lambda: improvements.approve(db.session, current_actor(), id, today=local_today())):
        flash('Mejora aprobada.', 'success')
    return _page(id)


@bp.route('/<int:id>/implantar', methods=['POST'])
@login_required
@require_permission('update', 'improvements')
def implantar_mejora(id):
    form = ImplantacionMejoraForm()
    if _valid(form) and committed(lambda: improvements.implement(
            db.session, current_actor(), id, fecha=form.fecha_implementacion.data, today=local_today())):
        flash('Implantación de la mejora registrada.', 'success')
    return _page(id)


@bp.route('/<int:id>/eficacia', methods=['POST'])
@login_required
@require_permission('update', 'improvements')
def revisar_mejora(id):
    form = EficaciaMejoraForm()
    if _valid(form) and committed(lambda: improvements.review_effectiveness(
            db.session, current_actor(), id, eficacia=form.eficacia.data, fecha=form.fecha_eficacia.data,
            comentario=form.comentario_eficacia.data, today=local_today())):
        flash('Eficacia de la mejora revisada.', 'success')
    return _page(id)


@bp.route('/<int:id>/cancelar', methods=['POST'])
@login_required
@require_permission('update', 'improvements')
def cancelar_mejora(id):
    form = CancelacionMejoraForm()
    if _valid(form) and committed(lambda: improvements.cancel(
            db.session, current_actor(), id, motivo=form.motivo_cancelacion.data, today=local_today())):
        flash('Mejora cancelada.', 'success')
    return _page(id)


class _JsonImprovements:
    """Parse the JSON date at the boundary; the service only accepts real dates."""
    get = staticmethod(improvements.get)
    list_page = staticmethod(improvements.list_page)
    delete = staticmethod(improvements.delete)

    @staticmethod
    def _data(data):
        if isinstance(data, Mapping) and isinstance(data.get('fecha_identificacion'), str):
            try:
                return {**data, 'fecha_identificacion': date.fromisoformat(data['fecha_identificacion'])}
            except ValueError:
                pass  # The service returns its normal Spanish validation error.
        return data

    @classmethod
    def create(cls, session, actor, data):
        return improvements.create(session, actor, cls._data(data), today=local_today())

    @classmethod
    def update(cls, session, actor, record_id, data):
        return improvements.update(session, actor, record_id, cls._data(data), today=local_today())


register_json_api(bp, _JsonImprovements, MejoraSchema, 'Mejora eliminada correctamente',
                  rule='/api/', prefix='api_')
