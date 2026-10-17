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

import enum
from decimal import Decimal

from .models import (
    ESCALA_MAX, ESCALA_MIN, CompetenceEvaluation, CompetenceType, DecisionOportunidad,
    DocumentCategory, EficaciaAccion, EstadoAccionRevision, EstadoAccionRiesgo,
    EstadoAuditoriaEnum, EstadoOportunidad, EstadoRiesgo, FrecuenciaMedicion,
    GravedadNoConformidad, OrigenNoConformidad, OrigenRiesgo, ResultadoVerificacion, RoleEnum,
    SentidoObjetivo, TipoAccionRevision, TipoHallazgo, TratamientoRiesgo,
)
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, BooleanField, SubmitField, DateField, IntegerField, SelectField, EmailField, SelectMultipleField
from wtforms import DecimalField, widgets
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, EqualTo, Email, Optional
# Person pickers (``persona_id``, ``responsable_id``, ``auditor_id``): the
# routes fill the choices with ``person_choices``; the empty choice means no
# person. The legacy free-text name next to each picker is optional here: the
# service requires it only when no person is picked.
NO_PERSON = ('', '— Sin persona —')
CHOOSE_PERSON = ('', '— Elige una persona —')  # for pickers where a person is required


def optional_id(value):
    """Select value to a person id; the empty choice means no person (``None``)."""
    if value is None or value == '':
        return None
    return int(value)


def person_choices(people, empty=NO_PERSON):
    """Picker choices: the ``empty`` one, then each person; inactive ones are marked."""
    return [empty, *((p.id, p.nombre if p.activo else f'{p.nombre} (desactivada)')
                     for p in people)]


def person_field(label):
    return SelectField(label, coerce=optional_id, choices=[NO_PERSON])


class BaseForm(FlaskForm):
    """Base form: validation messages in Spanish.

    Uses the translations bundled with WTForms (Flask-WTF's own i18n needs
    Flask-Babel, so it is disabled with WTF_I18N_ENABLED = False).
    """

    class Meta:
        locales = ('es_ES', 'es')


# Formulario de Inicio de Sesión
class LoginForm(BaseForm):
    username = StringField('Usuario', validators=[DataRequired(), Length(min=4, max=150)])
    password = PasswordField('Contraseña', validators=[DataRequired(), Length(min=6)])
    submit = SubmitField('Iniciar Sesión')

# Formulario para ParteInteresada
class ParteInteresadaForm(BaseForm):
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=50)])
    necesidades_expectativas = TextAreaField('Necesidades y Expectativas')
    requisitos_identificados = TextAreaField('Requisitos Identificados')
    objetivo_estrategico = TextAreaField('Objetivo Estratégico')
    submit = SubmitField('Guardar')

# Formulario para ingresar datos de Auditoría
NO_PROGRAMME = ('', '— Sin programa —')


def programme_choices(programmes):
    """Picker choices for an audit's programme: none, then each year with its state."""
    return [NO_PROGRAMME, *((p.id, f'Programa {p.anio} ({p.estado.value})') for p in programmes)]


# Planning fields (``audit-programme``, AP2): the routes fill the programme,
# people and team choices; the service checks independence and the year.
class AuditoriaForm(BaseForm):
    area_auditada = StringField('Área Auditada', validators=[DataRequired(), Length(max=50)])
    fecha = DateField('Fecha', validators=[DataRequired()])
    programa_id = SelectField('Programa de auditoría', coerce=optional_id, choices=[NO_PROGRAMME])
    auditor = StringField('Auditor', validators=[Length(max=50)])
    auditor_id = person_field('Auditor (persona)')
    equipo_ids = SelectMultipleField('Equipo auditor', coerce=int, choices=[])
    responsable_area_id = person_field('Responsable del área auditada')
    alcance = TextAreaField('Alcance')
    criterios = TextAreaField('Criterios de auditoría')
    resultado = TextAreaField('Resultado', validators=[DataRequired()])
    accion_correctiva = TextAreaField('Acción Correctiva')
    estado = SelectField(
        'Estado',
        choices=[(e.name, e.value) for e in EstadoAuditoriaEnum],
        validators=[DataRequired()]
    )
    submit = SubmitField('Guardar')


# Formulario para registrar No Conformidades
def member_name(value):
    """Select value from a stored enum member (its name), a posted name or nothing (``''``)."""
    if isinstance(value, enum.Enum):
        return value.name
    return '' if value is None else str(value)


class OptionalEnumField(SelectField):
    """Select over an enum's member names; the empty choice means none.

    The record's member (or its absence) becomes the selected name, so an edit
    that does not post the field keeps it valid instead of failing the choice.
    """

    def process_data(self, value):
        self.data = member_name(value)


def optional_enum_field(label, enum_cls, empty_label):
    return OptionalEnumField(label, coerce=member_name,
                             choices=[('', empty_label), *((m.name, m.value) for m in enum_cls)])


# The state is not a field: the service sets it (see ``nonconformities``).
class NoConformidadForm(BaseForm):
    descripcion = TextAreaField('Descripción', validators=[DataRequired()])
    fecha_detectada = DateField('Fecha Detectada', validators=[DataRequired()])
    origen = optional_enum_field('Origen', OrigenNoConformidad, '— Sin indicar —')
    gravedad = optional_enum_field('Gravedad', GravedadNoConformidad, '— Sin indicar —')
    responsable = StringField('Responsable', validators=[Length(max=50)])
    responsable_id = person_field('Responsable (persona)')
    contencion = TextAreaField('Contención')
    causa_raiz = TextAreaField('Causa raíz')
    accion_correctiva = TextAreaField('Acción Correctiva')
    submit = SubmitField('Guardar')

# Corrective actions of a nonconformity (decisions N3 and N4 of nc-capa-loop).
# The routes fill the person pickers; the service has the last word on every
# value (active owner, done date not before the detection, verifier other than
# the owner, verification date not before the done date).
class AccionCorrectivaForm(BaseForm):
    descripcion = TextAreaField('Descripción', validators=[DataRequired(), Length(max=1000)])
    responsable_id = SelectField('Responsable', coerce=optional_id, choices=[CHOOSE_PERSON],
                                 validators=[DataRequired()])
    fecha_prevista = DateField('Fecha prevista', validators=[DataRequired()])
    fecha_realizada = DateField('Fecha de realización', validators=[Optional()])


class VerificacionAccionForm(BaseForm):
    resultado_verificacion = SelectField(
        'Resultado',
        choices=[('', '— Elige un resultado —'),
                 *((result.name, result.value) for result in ResultadoVerificacion)],
        validators=[DataRequired()],
    )
    fecha_verificacion = DateField('Fecha de verificación', validators=[DataRequired()])
    verificador_id = SelectField('Verificada por', coerce=optional_id, choices=[CHOOSE_PERSON],
                                 validators=[DataRequired()])
    evidencia_verificacion = TextAreaField('Evidencia', validators=[DataRequired()])


# Formulario para registrar Mejoras (acciones correctivas y preventivas)
class MejoraForm(BaseForm):
    no_conformidad = TextAreaField('No conformidad', validators=[DataRequired()])
    accion_correctiva = TextAreaField('Acción correctiva')
    accion_preventiva = TextAreaField('Acción preventiva')
    submit = SubmitField('Guardar')


# Formulario para encuestas de Satisfacción del Cliente
class SatisfaccionClienteForm(BaseForm):
    cliente = StringField('Cliente', validators=[DataRequired(), Length(max=100)])
    fecha_encuesta = DateField('Fecha de Encuesta', validators=[DataRequired()])
    puntuacion = IntegerField('Puntuación (1-10)', validators=[DataRequired(), NumberRange(min=1, max=10)])
    comentarios = TextAreaField('Comentarios')
    submit = SubmitField('Guardar')

# Formulario para registrar Capacitaciones del Personal
class CapacitacionForm(BaseForm):
    tema = StringField('Tema', validators=[DataRequired(), Length(max=100)])
    fecha = DateField('Fecha', validators=[DataRequired()])
    personal = StringField('Personal', validators=[Length(max=100)])
    persona_id = person_field('Persona')
    duracion_horas = IntegerField('Duración en Horas', validators=[Optional(), NumberRange(min=0)])
    evaluacion_final = StringField('Evaluación Final', validators=[Length(max=20)])
    submit = SubmitField('Guardar')

# Formulario para el registro y actualización de documentos
# A document's own fields; its text lives in revisions (``document-control``).
# The routes fill the person pickers; the service has the last word.
class DocumentForm(BaseForm):
    title = StringField('Título', validators=[DataRequired(), Length(max=150)])
    code = StringField('Código de Identificación', validators=[DataRequired(), Length(max=50)])
    category = SelectField('Categoría', choices=[(cat.name, cat.value) for cat in DocumentCategory], validators=[DataRequired()])
    owner_id = SelectField('Propietario', coerce=optional_id, choices=[CHOOSE_PERSON],
                           validators=[DataRequired()])
    next_review_date = DateField('Próxima revisión', validators=[Optional()])


# A new document also writes its draft revision 1.
class NewDocumentForm(DocumentForm):
    author_id = SelectField('Autor de la revisión 1', coerce=optional_id,
                            choices=[CHOOSE_PERSON], validators=[DataRequired()])
    content = TextAreaField('Contenido', validators=[DataRequired()])


# The revision workflow (decisions DC1-DC6 of document-control). The routes
# fill the person pickers; the ``document_revisions`` service has the last word
# (author required, approver never the author, reasons required).
def required_person_field(label):
    return SelectField(label, coerce=optional_id, choices=[CHOOSE_PERSON],
                       validators=[DataRequired()])


class NewRevisionForm(BaseForm):
    author_id = required_person_field('Autor de la revisión')
    change_summary = TextAreaField('Resumen de cambios')


class DraftRevisionForm(BaseForm):
    content = TextAreaField('Contenido', validators=[DataRequired()])
    change_summary = TextAreaField('Resumen de cambios (obligatorio para enviarla a revisión)')
    author_id = required_person_field('Autor de la revisión')


class ApproveRevisionForm(BaseForm):
    approver_id = required_person_field('Aprobada por')


class RejectRevisionForm(BaseForm):
    review_comment = TextAreaField('Motivo del rechazo', validators=[DataRequired()])


class WithdrawDocumentForm(BaseForm):
    withdrawn_reason = TextAreaField('Motivo de la baja', validators=[DataRequired()])

# Formulario para solicitar recuperación de contraseña
class PasswordResetRequestForm(BaseForm):
    email = StringField('Correo electrónico', validators=[DataRequired(), Email()])
    submit = SubmitField('Enviar enlace de recuperación')

# Formulario para establecer nueva contraseña
class PasswordResetForm(BaseForm):
    password = PasswordField('Nueva contraseña', validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField('Confirmar contraseña', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Restablecer contraseña')

# Administrator forms for user accounts. The users service has the last word on
# every value (trimmed username, e-mail format and uniqueness, guard rails).
ROLE_CHOICES = [(role.name, role.value) for role in RoleEnum]


class UserCreateForm(BaseForm):
    username = StringField('Nombre de usuario', validators=[DataRequired(), Length(min=4, max=150)])
    email = EmailField('Correo electrónico', validators=[DataRequired(), Length(max=255)])
    role = SelectField('Rol', choices=ROLE_CHOICES, default=RoleEnum.OPERATIVO.name,
                       validators=[DataRequired()])
    password = PasswordField('Contraseña', validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        'Confirmar contraseña',
        validators=[DataRequired(), EqualTo('password', message='Las contraseñas no coinciden.')],
    )
    submit = SubmitField('Crear usuario')


# Usernames cannot change after creation (U5), so the edit form omits them.
class UserEditForm(BaseForm):
    email = EmailField('Correo electrónico', validators=[DataRequired(), Length(max=255)])
    role = SelectField('Rol', choices=ROLE_CHOICES, validators=[DataRequired()])
    submit = SubmitField('Guardar')


# "Mi perfil": every user changes their own e-mail and password (the users
# service checks the current password and has the last word on every value).
# Field names are unique across the page's two forms, so ids and labels match.
class EmailChangeForm(BaseForm):
    email = EmailField('Nuevo correo electrónico', validators=[DataRequired(), Length(max=255)])
    email_current_password = PasswordField('Contraseña actual', validators=[DataRequired()])


class PasswordChangeForm(BaseForm):
    current_password = PasswordField('Contraseña actual', validators=[DataRequired()])
    new_password = PasswordField('Nueva contraseña', validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        'Confirmar nueva contraseña',
        validators=[DataRequired(),
                    EqualTo('new_password', message='Las contraseñas no coinciden.')],
    )


# People and competence (ISO 9001 clauses 5.3 and 7.2). The routes fill the
# choices from the services, which have the last word on every value; an
# empty choice means "none" (``optional_id``). Enum selects use member names.
NO_USER = ('', '— Sin usuario —')
NO_REQUIREMENT = ('', '— Sin requisito —')
NO_TRAINING = ('', '— Sin capacitación —')
NO_ROLE = ('', '— Elige un rol —')


def user_choices(users):
    """User-link choices: the empty one, then each account; inactive ones are marked."""
    return [NO_USER, *((u.id, u.username if u.active else f'{u.username} (desactivado)')
                       for u in users)]


def role_choices(roles):
    """Role choices (``RolResponsabilidad``) in the given order."""
    return [(role.id_rol, role.rol) for role in roles]


def requirement_choices(labels):
    """The empty choice, then each requirement (``labels``: id to text) by its text."""
    return [NO_REQUIREMENT, *sorted(labels.items(), key=lambda item: item[1].casefold())]


def training_choices(trainings):
    """The empty choice, then each training as «date · topic»."""
    return [NO_TRAINING, *((t.id, f'{t.fecha:%d/%m/%Y} · {t.tema}') for t in trainings)]


class PersonForm(BaseForm):
    nombre = StringField('Nombre completo', validators=[DataRequired(), Length(max=150)])
    email = EmailField('Correo electrónico', validators=[Optional(), Length(max=255)])
    rol_ids = SelectMultipleField('Roles', coerce=int, choices=[])
    user_id = SelectField('Usuario vinculado', coerce=optional_id, choices=[NO_USER])
    activo = BooleanField('Activa', default=True)
    notas = TextAreaField('Notas')


class CompetenceRequirementForm(BaseForm):
    rol_id = SelectField('Rol', coerce=optional_id, choices=[NO_ROLE],
                         validators=[DataRequired()])
    tipo = SelectField('Tipo', choices=[(t.name, t.value) for t in CompetenceType],
                       validators=[DataRequired()])
    descripcion = StringField('Descripción', validators=[DataRequired(), Length(max=500)])
    criterio = TextAreaField('Criterio para evidenciarla')


class CompetenceRecordForm(BaseForm):
    requisito_id = SelectField('Requisito de competencia', coerce=optional_id,
                               choices=[NO_REQUIREMENT])
    evidencia = StringField('Evidencia', validators=[DataRequired(), Length(max=500)])
    capacitacion_id = SelectField('Capacitación', coerce=optional_id, choices=[NO_TRAINING])
    fecha_obtencion = DateField('Fecha de obtención', validators=[DataRequired()])
    fecha_caducidad = DateField('Fecha de caducidad', validators=[Optional()])
    evaluacion_eficacia = SelectField(
        'Evaluación de la eficacia',
        choices=[(e.name, e.value) for e in CompetenceEvaluation],
        default=CompetenceEvaluation.pendiente.name,
        validators=[DataRequired()],
    )
    fecha_evaluacion = DateField('Fecha de evaluación', validators=[Optional()])
    evaluador_id = person_field('Evaluador')


# Audit programmes and findings (``audit-programme``, AP-3). The services
# validate again; these checks only give field-level messages.
class ProgramaAuditoriaForm(BaseForm):
    anio = IntegerField('Año', validators=[DataRequired(), NumberRange(min=2000, max=2100)])
    objetivos = TextAreaField('Objetivos', validators=[DataRequired()])
    alcance = TextAreaField('Alcance', validators=[DataRequired()])


class AprobarProgramaForm(BaseForm):
    aprobado_por_id = required_person_field('Aprobado por')


class HallazgoForm(BaseForm):
    tipo = SelectField('Tipo', coerce=member_name, validators=[DataRequired()],
                       choices=[(t.name, t.value) for t in TipoHallazgo])
    clausula = StringField('Cláusula de la norma (p. ej. 8.5.1)', validators=[Length(max=20)])
    descripcion = TextAreaField('Descripción', validators=[DataRequired()])
    evidencia = TextAreaField('Evidencia')


# Quality objectives and their measurements (``quality-objectives``, QO-2). The
# routes fill the process and people pickers; the service has the last word on
# every value (period, target fixed once measured, date never in the future).
# Numbers are typed with a decimal point or comma and shown without trailing
# zeros, the way ``number_text`` writes them.
NO_PROCESS = ('', '— Sin proceso —')
TARGET_DIRECTIONS = {
    SentidoObjetivo.mayor_igual: 'igual o superior a la meta',
    SentidoObjetivo.menor_igual: 'igual o inferior a la meta',
}


def number_text(value):
    """A decimal as Spanish text without trailing zeros (``Decimal('88.5000')`` is ``88,5``)."""
    if value is None:
        return ''
    value = Decimal(value)
    if value == 0:
        return '0'
    return format(value.normalize(), 'f').replace('.', ',')


def process_choices(processes):
    """The empty choice, then each process (``ProcesoOperacion``) in the given order."""
    return [NO_PROCESS, *((p.id_proceso, p.proceso) for p in processes)]


NOT_FINITE = 'Debe ser un número finito.'


class NumberField(DecimalField):
    """A decimal typed with a point or a comma; shown as ``number_text`` writes it.

    ``Decimal`` reads ``nan``, ``inf``, ``Infinity`` and ``sNaN``; they are a
    field error (``NOT_FINITE``), like text that is not a number.
    """

    widget = widgets.TextInput()

    def __init__(self, label=None, validators=None, **kwargs):
        kwargs.setdefault('render_kw', {'inputmode': 'decimal'})
        super().__init__(label, validators, places=None, **kwargs)

    def process_formdata(self, valuelist):
        super().process_formdata([value.strip().replace(',', '.') for value in valuelist])
        if self.data is not None and not self.data.is_finite():
            self.data = None
            raise ValueError(NOT_FINITE)

    def _value(self):
        if self.raw_data:
            return self.raw_data[0]
        return number_text(self.data)


class ObjetivoCalidadForm(BaseForm):
    titulo = StringField('Título', validators=[DataRequired(), Length(max=200)])
    descripcion = TextAreaField('Descripción')
    proceso_id = SelectField('Proceso', coerce=optional_id, choices=[NO_PROCESS])
    responsable_id = required_person_field('Responsable')
    indicador = StringField('Indicador', validators=[DataRequired(), Length(max=200)])
    unidad = StringField('Unidad (p. ej. %, días)', validators=[Length(max=30)])
    # ``meta`` names the WTForms options of every form, so the attribute is renamed.
    target = NumberField('Meta', validators=[InputRequired()], name='meta')
    sentido = SelectField('Sentido de la meta', coerce=member_name, validators=[DataRequired()],
                          choices=[(s.name, f'{s.value} {label}')
                                   for s, label in TARGET_DIRECTIONS.items()])
    valor_inicial = NumberField('Valor inicial', validators=[Optional()])
    frecuencia = SelectField('Frecuencia de medición', coerce=member_name,
                             validators=[DataRequired()],
                             choices=[(f.name, f.value) for f in FrecuenciaMedicion])
    fecha_inicio = DateField('Fecha de inicio', validators=[DataRequired()])
    fecha_limite = DateField('Fecha límite', validators=[DataRequired()])
    anio = IntegerField('Año (vacío: el de la fecha de inicio)',
                        validators=[Optional(), NumberRange(min=2000, max=2100)])
    recursos = TextAreaField('Recursos necesarios')
    evaluacion = TextAreaField('Cómo se evalúan los resultados')


class MedicionObjetivoForm(BaseForm):
    fecha = DateField('Fecha', validators=[DataRequired()])
    valor = NumberField('Valor', validators=[InputRequired()])
    registrado_por_id = person_field('Registrada por')
    comentario = TextAreaField('Comentario')


# Management reviews and their actions (``management-review``, MR-2). The routes
# fill the people pickers; the service has the last word on every value (the
# period ends by the meeting, active attendees and owners, a completion date
# only for a completed action). ``REVIEW_INPUTS`` names the inputs of ISO 9001
# clause 9.3.2 a) to f) by their comment field, for the labels and the page.
REVIEW_INPUTS = {
    'comentario_acciones_previas': 'a) Estado de las acciones de las revisiones previas',
    'comentario_contexto': 'b) Cambios en las cuestiones externas e internas',
    'comentario_desempeno': 'c) Desempeño y eficacia del SGC',
    'comentario_recursos': 'd) Adecuación de los recursos',
    'comentario_riesgos': 'e) Eficacia de las acciones para abordar riesgos y oportunidades',
    'comentario_mejora': 'f) Oportunidades de mejora',
}


class _ReviewMeeting(BaseForm):  # declared first, so its fields come first
    fecha = DateField('Fecha de la reunión', validators=[DataRequired()])
    periodo_desde = DateField('Periodo revisado: desde', validators=[DataRequired()])
    periodo_hasta = DateField('Periodo revisado: hasta', validators=[DataRequired()])
    asistente_ids = SelectMultipleField('Asistentes', coerce=int, choices=[])


class _ReviewComments(BaseForm):
    comentario_acciones_previas = TextAreaField(REVIEW_INPUTS['comentario_acciones_previas'])
    comentario_contexto = TextAreaField(REVIEW_INPUTS['comentario_contexto'])
    comentario_desempeno = TextAreaField(
        REVIEW_INPUTS['comentario_desempeno'] + ' (incluidos los proveedores externos)')
    comentario_recursos = TextAreaField(REVIEW_INPUTS['comentario_recursos'])
    comentario_riesgos = TextAreaField(REVIEW_INPUTS['comentario_riesgos'])
    comentario_mejora = TextAreaField(REVIEW_INPUTS['comentario_mejora'])


class RevisionDireccionForm(_ReviewMeeting, _ReviewComments):
    """A draft review: the meeting, its period, its attendees and a comment per input."""


class ConclusionesRevisionForm(_ReviewComments):
    """A held review: the comments per input and the conclusions."""

    conclusiones = TextAreaField('Conclusiones sobre la conveniencia, adecuación y eficacia '
                                 'del SGC')


class AccionRevisionForm(BaseForm):
    tipo = SelectField('Tipo', coerce=member_name, validators=[DataRequired()],
                       choices=[(t.name, t.value) for t in TipoAccionRevision])
    descripcion = TextAreaField('Descripción', validators=[DataRequired()])
    responsable_id = required_person_field('Responsable')
    fecha_limite = DateField('Fecha límite', validators=[DataRequired()])
    estado = SelectField('Estado', coerce=member_name, validators=[DataRequired()],
                         choices=[(s.name, s.value) for s in EstadoAccionRevision])
    fecha_completada = DateField('Fecha de finalización (solo si está completada)',
                                 validators=[Optional()])
    resultado = TextAreaField('Resultado')


# Risks, opportunities and their actions (``risks-opportunities``, RO-3). The
# routes fill the interested party, process and people pickers; the service has
# the last word on every value (each 1-5 pair given whole or left empty, the
# residual pair after the assessment, the review not before the identification,
# completion and effectiveness dates never in the future).
NO_PARTY = ('', '— Sin parte interesada —')
NOT_ASSESSED = ('', 'Sin valorar')


def party_choices(parties):
    """The empty choice, then each interested party (``ParteInteresada``) in the given order."""
    return [NO_PARTY, *((p.id_interesado, p.nombre) for p in parties)]


def optional_member(value):
    """Select value to a member name, or ``None`` for the empty choice."""
    return member_name(value) or None


def _choice(label, enum_cls, empty=None):
    """A select over ``enum_cls`` by member name; with ``empty``, it may be left blank."""
    members = [(m.name, m.value) for m in enum_cls]
    if empty is None:
        return SelectField(label, coerce=member_name, choices=members,
                           validators=[DataRequired()])
    return SelectField(label, coerce=optional_member, choices=[('', empty), *members])


def _scale(label):
    """A 1-5 value of an assessment; the empty choice leaves it pending (RO4)."""
    return SelectField(label, coerce=optional_id, choices=[
        NOT_ASSESSED, *((n, str(n)) for n in range(ESCALA_MIN, ESCALA_MAX + 1))])


class _RegisterRecord(BaseForm):  # the field order follows the declarations
    titulo = StringField('Título', validators=[DataRequired(), Length(max=200)])
    descripcion = TextAreaField('Descripción', validators=[DataRequired()])
    origen = _choice('Origen', OrigenRiesgo)
    parte_interesada_id = SelectField('Parte interesada', coerce=optional_id,
                                      choices=[NO_PARTY])
    proceso_id = SelectField('Proceso', coerce=optional_id, choices=[NO_PROCESS])
    responsable_id = required_person_field('Responsable')


class _RiskAssessment(BaseForm):
    probabilidad = _scale('Probabilidad (1-5)')
    impacto = _scale('Impacto (1-5)')
    tratamiento = _choice('Tratamiento', TratamientoRiesgo, '— Sin decidir —')
    probabilidad_residual = _scale('Probabilidad residual (tras las acciones)')
    impacto_residual = _scale('Impacto residual (tras las acciones)')
    estado = _choice('Estado', EstadoRiesgo)


class _OpportunityAssessment(BaseForm):
    beneficio = _scale('Beneficio (1-5)')
    viabilidad = _scale('Viabilidad (1-5)')
    decision = _choice('Decisión', DecisionOportunidad, '— Sin decidir —')
    estado = _choice('Estado', EstadoOportunidad)


class _RegisterDates(BaseForm):
    fecha_identificacion = DateField('Fecha de identificación', validators=[DataRequired()])
    fecha_revision = DateField('Próxima revisión', validators=[Optional()])
    notas = TextAreaField('Notas')


class RiesgoForm(_RegisterRecord, _RiskAssessment, _RegisterDates):
    """A risk: what and whose it is, its assessments, state, dates and notes."""


class OportunidadForm(_RegisterRecord, _OpportunityAssessment, _RegisterDates):
    """An opportunity: what and whose it is, its assessment, decision, state and dates."""


class AccionRiesgoForm(BaseForm):
    descripcion = TextAreaField('Descripción', validators=[DataRequired()])
    responsable_id = required_person_field('Responsable')
    fecha_limite = DateField('Fecha límite', validators=[DataRequired()])
    estado = _choice('Estado', EstadoAccionRiesgo)
    fecha_completada = DateField('Fecha de finalización (solo si está completada)',
                                 validators=[Optional()])


class EficaciaAccionForm(BaseForm):
    eficacia = SelectField('Resultado', coerce=member_name, validators=[DataRequired()],
                           choices=[('', '— Elige un resultado —'),
                                    *((e.name, e.value) for e in EficaciaAccion)])
    fecha_evaluacion = DateField('Fecha de la evaluación', validators=[DataRequired()])
    comentario_eficacia = TextAreaField('Comentario')
