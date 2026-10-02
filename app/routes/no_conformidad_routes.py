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


from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required
from ..models import RoleEnum
from ..forms import NoConformidadForm
from ..extensions import db
from ..services import nonconformities
from ..utils.decorators import role_required
from ..utils.pdf import pdf_response, render_pdf
from ..utils.web_actor import current_actor
from ..utils.web_args import date_arg

# Define el blueprint y la URL base
bp = Blueprint('no_conformidad', __name__, url_prefix='/no_conformidades')

FORM_FIELDS = ('descripcion', 'fecha_detectada', 'responsable', 'estado', 'accion_correctiva')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


# Ruta para listar todas las no conformidades
@bp.route('/', methods=['GET'])
@login_required
def listar_no_conformidades():
    actor = current_actor()
    no_conformidades = nonconformities.list_(
        db.session,
        actor,
        descripcion=request.args.get('descripcion'),
        estado=request.args.get('estado'),
        fecha_detectada=date_arg('fecha_detectada'),
    )
    return render_template('no_conformidades/listar.html', no_conformidades=no_conformidades,
                           estados=nonconformities.available_states(db.session, actor))

# Ruta para registrar una nueva no conformidad
@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
def nueva_no_conformidad():
    form = NoConformidadForm()
    if form.validate_on_submit():
        nonconformities.create(db.session, current_actor(), _form_data(form))
        db.session.commit()
        flash('No conformidad registrada exitosamente', 'success')
        return redirect(url_for('no_conformidad.listar_no_conformidades'))
    return render_template('no_conformidades/nueva.html', form=form)

# Ruta para editar una no conformidad
@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_no_conformidad(id):
    actor = current_actor()
    no_conformidad = nonconformities.get(db.session, actor, id)
    form = NoConformidadForm(obj=no_conformidad)
    # Records created when the state was free text keep their value
    # unless the user picks another one.
    if no_conformidad.estado not in nonconformities.ESTADOS_NO_CONFORMIDAD:
        form.estado.choices = [
            (no_conformidad.estado, f'{no_conformidad.estado} (heredado)'),
            *form.estado.choices,
        ]
    if form.validate_on_submit():
        nonconformities.update(db.session, actor, id, _form_data(form))
        db.session.commit()
        flash('No conformidad actualizada exitosamente', 'success')
        return redirect(url_for('no_conformidad.listar_no_conformidades'))
    return render_template('no_conformidades/editar.html', form=form, no_conformidad=no_conformidad)

# Ruta para eliminar una no conformidad
@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@role_required(RoleEnum.ADMINISTRADOR)
def eliminar_no_conformidad(id):
    nonconformities.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('No conformidad eliminada exitosamente', 'success')
    return redirect(url_for('no_conformidad.listar_no_conformidades'))

# Ruta para exportar una no conformidad a PDF
@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
def exportar_pdf(id):
    no_conformidad = nonconformities.get(db.session, current_actor(), id)
    return pdf_response(render_pdf('no_conformidades/pdf_template.html', no_conformidad=no_conformidad), f'no_conformidad_{id}.pdf')
