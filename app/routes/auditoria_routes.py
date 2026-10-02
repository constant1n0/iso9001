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

from datetime import date
from math import ceil

from flask import Blueprint, current_app, render_template, redirect, url_for, flash, request
from flask_login import login_required
from ..models import RoleEnum, EstadoAuditoriaEnum
from ..forms import AuditoriaForm
from ..extensions import db
from ..services import audits
from ..utils.decorators import role_required
from ..utils.pdf import pdf_response, render_pdf
from ..utils.web_actor import current_actor

bp = Blueprint('auditoria', __name__, url_prefix='/auditorias')

FORM_FIELDS = ('area_auditada', 'fecha', 'auditor', 'resultado', 'accion_correctiva', 'estado')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


def _date_arg(name):
    """Query-string date, or None when absent or malformed."""
    try:
        return date.fromisoformat(request.args.get(name, ''))
    except ValueError:
        return None


def _estado_arg():
    """Query-string state as an enum member, or None when absent or unknown."""
    try:
        return EstadoAuditoriaEnum(request.args.get('estado'))
    except ValueError:
        return None


@bp.route('/', methods=['GET'])
@login_required
@role_required(RoleEnum.AUDITOR)
def listar_auditorias():
    """
    Lista todas las auditorías registradas en el sistema, con funcionalidad de búsqueda y filtrado avanzado.
    Incluye paginación para manejar grandes volúmenes de datos.
    """
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Número de auditorías por página

    auditorias, total_auditorias = audits.list_page(
        db.session,
        current_actor(),
        area=request.args.get('area'),
        auditor=request.args.get('auditor'),
        estado=_estado_arg(),
        fecha_inicio=_date_arg('fecha_inicio'),
        fecha_fin=_date_arg('fecha_fin'),
        page=page,
        per_page=per_page,
    )

    return render_template(
        'auditorias/listar.html', 
        auditorias=auditorias, 
        page=page, 
        total_pages=ceil(total_auditorias / per_page),
        total_auditorias=total_auditorias
    )

@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@role_required(RoleEnum.AUDITOR)
def nueva_auditoria():
    """
    Muestra el formulario para crear una nueva auditoría y guarda el registro
    en la base de datos al enviarlo.
    """
    form = AuditoriaForm()
    if form.validate_on_submit():
        audits.create(db.session, current_actor(), _form_data(form))
        db.session.commit()
        flash('Auditoría creada exitosamente', 'success')
        return redirect(url_for('auditoria.listar_auditorias'))
    return render_template('auditorias/nueva.html', form=form)

@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@role_required(RoleEnum.AUDITOR)
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

    if form.validate_on_submit():
        audits.update(db.session, actor, id, _form_data(form))
        db.session.commit()
        flash('Auditoría actualizada exitosamente', 'success')
        return redirect(url_for('auditoria.listar_auditorias'))
    return render_template('auditorias/editar.html', form=form, auditoria=auditoria)

@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@role_required(RoleEnum.AUDITOR)
def eliminar_auditoria(id):
    """
    Elimina una auditoría existente de la base de datos.
    """
    audits.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('Auditoría eliminada exitosamente', 'success')
    return redirect(url_for('auditoria.listar_auditorias'))

@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
@role_required(RoleEnum.AUDITOR)
def exportar_pdf(id):
    """
    Genera un PDF para una auditoría específica usando su ID.
    """
    auditoria = audits.get(db.session, current_actor(), id)

    try:
        return pdf_response(render_pdf('auditorias/pdf_template.html', auditoria=auditoria), f'auditoria_{id}.pdf')
    except Exception:
        current_app.logger.exception('Error generating PDF for audit %s', id)
        flash('Error al generar el PDF de la auditoría.', 'danger')
        return redirect(url_for('auditoria.listar_auditorias'))
