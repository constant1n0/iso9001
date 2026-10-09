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
from ..audit_notifications import local_today
from ..forms import NoConformidadForm, person_choices
from ..extensions import db
from ..models import EstadoNoConformidad, GravedadNoConformidad, OrigenNoConformidad
from ..services import corrective_actions, nonconformities, people
from ..services.errors import ValidationError
from ..utils.permissions import require_permission
from ..utils.pdf import pdf_response, render_pdf
from ..utils.web_actor import current_actor
from ..utils.web_args import date_arg
from .corrective_action_routes import may_verify

# Define el blueprint y la URL base
bp = Blueprint('no_conformidad', __name__, url_prefix='/no_conformidades')

# The state is never posted: the service sets it, and cancel/reopen change it.
FORM_FIELDS = ('descripcion', 'fecha_detectada', 'origen', 'gravedad', 'responsable',
               'responsable_id', 'contencion', 'causa_raiz', 'accion_correctiva')


def _form_data(form):
    """Whitelisted service payload taken from a validated form."""
    return {name: getattr(form, name).data for name in FORM_FIELDS}


def _saved(write):
    """Run a service write that returns its record, commit, and return the record.

    On a ``ValidationError`` roll back, flash why and return ``None``; the route
    then shows the form again, keeping what the user typed (or, for a one-button
    action, the nonconformity's page with the reason). Other domain errors reach
    the global handlers.
    """
    try:
        written = write()
        db.session.commit()
    except ValidationError as error:
        db.session.rollback()
        flash(error.message, 'danger')
        return None
    return written


def _page(id):
    """Back to the nonconformity's page."""
    return redirect(url_for('no_conformidad.ver_no_conformidad', id=id))


def _people_of(actor, no_conformidad, acciones):
    """Names of the people a nonconformity and its actions cite (owner, verifiers)."""
    person_ids = [no_conformidad.responsable_id, *(a.responsable_id for a in acciones),
                  *(a.verificador_id for a in acciones)]
    return people.names(db.session, actor, person_ids)


def _enum_arg(name, enum_cls):
    """The enum member named by query argument ``name``, or ``None``.

    An unknown name (an old bookmark, a typo) is ignored, like an invalid date.
    """
    return enum_cls.__members__.get(request.args.get(name, ''))


# Ruta para listar todas las no conformidades
@bp.route('/', methods=['GET'])
@login_required
def listar_no_conformidades():
    actor = current_actor()
    no_conformidades = nonconformities.list_(
        db.session,
        actor,
        descripcion=request.args.get('descripcion'),
        estado=_enum_arg('estado', EstadoNoConformidad),
        origen=_enum_arg('origen', OrigenNoConformidad),
        gravedad=_enum_arg('gravedad', GravedadNoConformidad),
        fecha_detectada=date_arg('fecha_detectada'),
    )
    personas = people.names(db.session, actor, (nc.responsable_id for nc in no_conformidades))
    return render_template('no_conformidades/listar.html', no_conformidades=no_conformidades,
                           estados=list(EstadoNoConformidad),
                           origenes=list(OrigenNoConformidad),
                           gravedades=list(GravedadNoConformidad), personas=personas)

# Ruta para registrar una nueva no conformidad
@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'nonconformities')
def nueva_no_conformidad():
    actor = current_actor()
    form = NoConformidadForm()
    form.responsable_id.choices = person_choices(people.choices(db.session, actor))
    created = form.validate_on_submit() and _saved(
        lambda: nonconformities.create(db.session, actor, _form_data(form))
    )
    if created:
        flash('No conformidad registrada exitosamente', 'success')
        return _page(created.id)
    return render_template('no_conformidades/nueva.html', form=form)


# The nonconformity's page: every field, its corrective actions and the
# buttons the role and the state allow (decisions N3-N5 of nc-capa-loop).
@bp.route('/<int:id>', methods=['GET'])
@login_required
@require_permission('read', 'nonconformities')
def ver_no_conformidad(id):
    actor = current_actor()
    no_conformidad = nonconformities.get(db.session, actor, id)
    acciones = corrective_actions.list_(db.session, actor, no_conformidad_id=id)
    return render_template(
        'no_conformidades/detalle.html', no_conformidad=no_conformidad, acciones=acciones,
        personas=_people_of(actor, no_conformidad, acciones),
        abierta=no_conformidad.estado not in nonconformities.TERMINAL_STATES,
        puede_verificar=may_verify(),
        puede_cerrar=nonconformities.may_close(actor, no_conformidad),
        bloqueos=nonconformities.close_blockers(acciones),
        puede_cancelar=nonconformities.may_cancel(actor, no_conformidad),
        puede_reabrir=nonconformities.may_reopen(actor, no_conformidad),
    )

# Ruta para editar una no conformidad
@bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'nonconformities')
def editar_no_conformidad(id):
    actor = current_actor()
    no_conformidad = nonconformities.get(db.session, actor, id)
    form = NoConformidadForm(obj=no_conformidad)
    form.responsable_id.choices = person_choices(
        people.choices(db.session, actor, include=no_conformidad.responsable_id))
    if form.validate_on_submit() and _saved(
        lambda: nonconformities.update(db.session, actor, id, _form_data(form))
    ):
        flash('No conformidad actualizada exitosamente', 'success')
        return _page(id)
    return render_template(
        'no_conformidades/editar.html', form=form, no_conformidad=no_conformidad,
        solo_lectura=no_conformidad.estado in nonconformities.TERMINAL_STATES,
        puede_cancelar=nonconformities.may_cancel(actor, no_conformidad),
        puede_reabrir=nonconformities.may_reopen(actor, no_conformidad),
    )


# Cancel and reopen: refusals (permission, blank reason, wrong state) are
# domain errors that the error handlers flash before going back to the form.
@bp.route('/cancelar/<int:id>', methods=['POST'])
@login_required
@require_permission('update', 'nonconformities')
def cancelar_no_conformidad(id):
    nonconformities.cancel(db.session, current_actor(), id,
                           request.form.get('motivo_cancelacion', ''), today=local_today())
    db.session.commit()
    flash('No conformidad cancelada.', 'success')
    return _page(id)


@bp.route('/reabrir/<int:id>', methods=['POST'])
@login_required
@require_permission('update', 'nonconformities')
def reabrir_no_conformidad(id):
    nonconformities.reopen(db.session, current_actor(), id)
    db.session.commit()
    flash('No conformidad reabierta.', 'success')
    return _page(id)


# Closing (administrators and auditors, once every action proved effective):
# what is still missing comes back as a flash on the nonconformity's page.
@bp.route('/cerrar/<int:id>', methods=['POST'])
@login_required
@require_permission('update', 'nonconformities')
def cerrar_no_conformidad(id):
    if _saved(lambda: nonconformities.close(db.session, current_actor(), id,
                                            today=local_today())):
        flash('No conformidad cerrada.', 'success')
    return _page(id)

# Ruta para eliminar una no conformidad
@bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@require_permission('delete', 'nonconformities')
def eliminar_no_conformidad(id):
    nonconformities.delete(db.session, current_actor(), id)
    db.session.commit()
    flash('No conformidad eliminada exitosamente', 'success')
    return redirect(url_for('no_conformidad.listar_no_conformidades'))

# Ruta para exportar una no conformidad a PDF: its fields, the closing or
# cancellation, and every corrective action with its verification.
@bp.route('/exportar_pdf/<int:id>', methods=['GET'])
@login_required
def exportar_pdf(id):
    actor = current_actor()
    no_conformidad = nonconformities.get(db.session, actor, id)
    acciones = corrective_actions.list_(db.session, actor, no_conformidad_id=id)
    pdf = render_pdf('no_conformidades/pdf_template.html', no_conformidad=no_conformidad,
                     acciones=acciones, personas=_people_of(actor, no_conformidad, acciones))
    return pdf_response(pdf, f'no_conformidad_{id}.pdf')
