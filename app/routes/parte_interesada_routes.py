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

"""Interested party screens (ISO 9001 clause 4.2): adapters over ``stakeholders``.

Every role reads and writes interested parties; administrators delete (policy
``interested_parties``). OC-3 of ``organization-context`` adds the context
fields of decision OC2 (type, relevance, owner, next review and climate-related
requirements) to the form and to the party's page, their badges to the list,
and the list's ``revision_vencida`` filter (``1``), which the dashboard links.
A refused write re-renders the form with what was typed and the reason flashed.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import ParteInteresadaForm, person_choices
from ..models import ParteInteresada
from ..services import people, stakeholders
from ..services.actor import Actor
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .people_routes import form_data, saved

bp = Blueprint('parte_interesada', __name__, url_prefix='/partes_interesadas')

RESOURCE = 'interested_parties'
FORM_FIELDS = ('nombre', 'necesidades_expectativas', 'requisitos_identificados',
               'objetivo_estrategico', 'tipo', 'pertinente', 'responsable_id',
               'fecha_revision', 'requisitos_climaticos')


def _list_page() -> ResponseReturnValue:
    return redirect(url_for('parte_interesada.listar_partes_interesadas'))


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def listar_partes_interesadas() -> ResponseReturnValue:
    """Every party by name; ``revision_vencida=1`` keeps those whose review is overdue."""
    actor, today = current_actor(), local_today()
    partes = stakeholders.list_(db.session, actor)
    vencidas = {p.id_interesado for p in partes if stakeholders.revision_vencida(p, today=today)}
    solo_vencidas = request.args.get('revision_vencida') == '1'
    if solo_vencidas:
        partes = [p for p in partes if p.id_interesado in vencidas]
    return render_template(
        'partes_interesadas/listar.html', partes=partes, vencidas=vencidas,
        solo_vencidas=solo_vencidas,
        personas=people.names(db.session, actor, (p.responsable_id for p in partes)))


@bp.route('/<int:id>', methods=['GET'])
@login_required
@require_permission('read', RESOURCE)
def ver_parte_interesada(id: int) -> ResponseReturnValue:
    """A party's page: its context fields and texts."""
    actor = current_actor()
    parte = stakeholders.get(db.session, actor, id)
    return render_template(
        'partes_interesadas/ver.html', parte=parte,
        vencida=stakeholders.revision_vencida(parte, today=local_today()),
        personas=people.names(db.session, actor, (parte.responsable_id,)))


def _form(actor: Actor, parte: ParteInteresada | None = None) -> ParteInteresadaForm:
    """The party form; the owner picker offers active people and the stored one."""
    form = ParteInteresadaForm(obj=parte)
    form.responsable_id.choices = person_choices(people.choices(
        db.session, actor, include=getattr(parte, 'responsable_id', None)))
    return form


@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCE)
def crear_parte_interesada() -> ResponseReturnValue:
    actor = current_actor()
    form = _form(actor)
    # The adapter's date: a new next review is never before it (OC2).
    if form.validate_on_submit() and saved(lambda: stakeholders.create(
            db.session, actor, form_data(form, FORM_FIELDS), today=local_today())):
        flash('Parte interesada creada exitosamente', 'success')
        return _list_page()
    return render_template('partes_interesadas/nueva.html', form=form)


@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCE)
def editar_parte_interesada(id: int) -> ResponseReturnValue:
    actor = current_actor()
    parte = stakeholders.get(db.session, actor, id)
    form = _form(actor, parte)
    if form.validate_on_submit() and saved(lambda: stakeholders.update(
            db.session, actor, id, form_data(form, FORM_FIELDS), today=local_today())):
        flash('Parte interesada actualizada exitosamente', 'success')
        return _list_page()
    return render_template('partes_interesadas/editar.html', form=form, parte=parte)


@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_parte_interesada(id: int) -> ResponseReturnValue:
    stakeholders.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('Parte interesada eliminada correctamente', 'success')
    return _list_page()
