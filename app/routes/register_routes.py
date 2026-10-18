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

"""Training-resource and audit-indicator screens: adapters over two legacy services.

Task RG-3 of ``process-role-registers`` (decision PR4): list, detail and form
screens with the registers' current fields only. Training resources (ISO 9001
clause 7.1) live under ``/recursos-formacion`` (blueprint ``recurso_formacion``)
and audit indicators (clause 9.1) under ``/indicadores-auditoria``
(``indicador_auditoria``); the JSON registers at ``/recurso_capacitacion/`` and
``/auditoria_indicador/`` stay for compatibility. Every role reads both;
administrators and auditors create and edit; administrators delete (policies
``TRAINING_RESOURCES`` and ``AUDIT_INDICATORS``, decision PR5). Nothing cites
these records, so every one can be deleted.

- Training resources are listed by resource, ignoring case.
- Audit indicators are listed newest first. Their list says the register is
  legacy: new process indicators belong in quality objectives. The stored audit
  date has a time and the form takes a day, so a new indicator gets midnight
  and an edit keeping the day keeps the stored time.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed.
"""

from datetime import datetime, time
from typing import Any

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import IndicadorAuditoriaForm, RecursoCapacitacionForm
from ..models import AuditoriaIndicador
from ..services import audit_indicators, training_resources
from ..utils.permissions import require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .people_routes import form_data, saved

resources_bp = Blueprint('recurso_formacion', __name__, url_prefix='/recursos-formacion')
indicators_bp = Blueprint('indicador_auditoria', __name__, url_prefix='/indicadores-auditoria')

RESOURCES, INDICATORS = 'training_resources', 'audit_indicators'
RESOURCE_FIELDS = ('recurso_necesario', 'capacitacion_personal', 'descripcion_documentacion')
INDICATOR_TEXTS = ('area_auditoria', 'resultado', 'accion_correctiva', 'indicador_desempeno')


# -- training resources ----------------------------------------------------------------


def _resource_page(resource_id: int) -> ResponseReturnValue:
    return redirect(url_for('recurso_formacion.show_resource', resource_id=resource_id))


@resources_bp.route('/', methods=['GET'])
@login_required
@require_permission('read', RESOURCES)
def list_resources() -> ResponseReturnValue:
    """Every training resource, by resource ignoring case."""
    found = sorted(training_resources.list_(db.session, current_actor()),
                   key=lambda r: (r.recurso_necesario.casefold(), r.id_recurso))
    return render_template('recursos_capacitacion/list.html', recursos=found)


@resources_bp.route('/<int:resource_id>', methods=['GET'])
@login_required
@require_permission('read', RESOURCES)
def show_resource(resource_id: int) -> ResponseReturnValue:
    recurso = training_resources.get(db.session, current_actor(), resource_id)
    return render_template('recursos_capacitacion/detail.html', recurso=recurso)


@resources_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', RESOURCES)
def new_resource() -> ResponseReturnValue:
    form = RecursoCapacitacionForm()
    if form.validate_on_submit() and (created := saved(lambda: training_resources.create(
            db.session, current_actor(), form_data(form, RESOURCE_FIELDS)))):
        flash('Recurso de capacitación creado.', 'success')
        return _resource_page(created.id_recurso)
    return render_template('recursos_capacitacion/form.html', form=form, recurso=None)


@resources_bp.route('/<int:resource_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', RESOURCES)
def edit_resource(resource_id: int) -> ResponseReturnValue:
    actor = current_actor()
    recurso = training_resources.get(db.session, actor, resource_id)
    form = RecursoCapacitacionForm(obj=recurso)
    if form.validate_on_submit() and saved(lambda: training_resources.update(
            db.session, actor, resource_id, form_data(form, RESOURCE_FIELDS))):
        flash('Recurso de capacitación actualizado.', 'success')
        return _resource_page(resource_id)
    return render_template('recursos_capacitacion/form.html', form=form, recurso=recurso)


@resources_bp.route('/<int:resource_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', RESOURCES)
def delete_resource(resource_id: int) -> ResponseReturnValue:
    """Delete a training resource (administrators)."""
    if committed(lambda: training_resources.delete(db.session, current_actor(), resource_id)):
        flash('Recurso de capacitación eliminado.', 'success')
        return redirect(url_for('recurso_formacion.list_resources'))
    return _resource_page(resource_id)


# -- audit indicators ------------------------------------------------------------------


def _indicator_page(indicator_id: int) -> ResponseReturnValue:
    return redirect(url_for('indicador_auditoria.show_indicator', indicator_id=indicator_id))


def _indicator_data(form: IndicadorAuditoriaForm,
                    stored: AuditoriaIndicador | None = None) -> dict[str, Any]:
    """The service payload: the texts and, unless the stored day is kept, the day at midnight."""
    data = form_data(form, INDICATOR_TEXTS)
    day = form.fecha_auditoria.data
    kept = getattr(stored, 'fecha_auditoria', None)
    if kept is None or kept.date() != day:
        data['fecha_auditoria'] = datetime.combine(day, time())
    return data


@indicators_bp.route('/', methods=['GET'])
@login_required
@require_permission('read', INDICATORS)
def list_indicators() -> ResponseReturnValue:
    """Every audit indicator, newest first; a record without a date goes last."""
    found = sorted(audit_indicators.list_(db.session, current_actor()),
                   key=lambda i: (i.fecha_auditoria or datetime.min, i.id_auditoria),
                   reverse=True)
    return render_template('indicadores_auditoria/list.html', indicadores=found)


@indicators_bp.route('/<int:indicator_id>', methods=['GET'])
@login_required
@require_permission('read', INDICATORS)
def show_indicator(indicator_id: int) -> ResponseReturnValue:
    indicador = audit_indicators.get(db.session, current_actor(), indicator_id)
    return render_template('indicadores_auditoria/detail.html', indicador=indicador)


@indicators_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@require_permission('create', INDICATORS)
def new_indicator() -> ResponseReturnValue:
    form = IndicadorAuditoriaForm()
    if request.method == 'GET':
        form.fecha_auditoria.data = local_today()
    if form.validate_on_submit() and (created := saved(lambda: audit_indicators.create(
            db.session, current_actor(), _indicator_data(form)))):
        flash('Indicador de auditoría creado.', 'success')
        return _indicator_page(created.id_auditoria)
    return render_template('indicadores_auditoria/form.html', form=form, indicador=None)


@indicators_bp.route('/<int:indicator_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', INDICATORS)
def edit_indicator(indicator_id: int) -> ResponseReturnValue:
    actor = current_actor()
    indicador = audit_indicators.get(db.session, actor, indicator_id)
    form = IndicadorAuditoriaForm(obj=indicador)
    if form.validate_on_submit() and saved(lambda: audit_indicators.update(
            db.session, actor, indicator_id, _indicator_data(form, indicador))):
        flash('Indicador de auditoría actualizado.', 'success')
        return _indicator_page(indicator_id)
    return render_template('indicadores_auditoria/form.html', form=form, indicador=indicador)


@indicators_bp.route('/<int:indicator_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', INDICATORS)
def delete_indicator(indicator_id: int) -> ResponseReturnValue:
    """Delete an audit indicator (administrators)."""
    if committed(lambda: audit_indicators.delete(db.session, current_actor(), indicator_id)):
        flash('Indicador de auditoría eliminado.', 'success')
        return redirect(url_for('indicador_auditoria.list_indicators'))
    return _indicator_page(indicator_id)
