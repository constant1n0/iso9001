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

from .models import (
    CompetenceEvaluation, CompetenceType, DocumentCategory, EstadoAuditoriaEnum,
    GravedadNoConformidad, OrigenNoConformidad, ResultadoVerificacion, RoleEnum,
)
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, BooleanField, SubmitField, DateField, IntegerField, SelectField, EmailField, SelectMultipleField
from wtforms.validators import DataRequired, Length, NumberRange, EqualTo, Email, Optional
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
class AuditoriaForm(BaseForm):
    area_auditada = StringField('Área Auditada', validators=[DataRequired(), Length(max=50)])
    fecha = DateField('Fecha', validators=[DataRequired()])
    auditor = StringField('Auditor', validators=[Length(max=50)])
    auditor_id = person_field('Auditor (persona)')
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
