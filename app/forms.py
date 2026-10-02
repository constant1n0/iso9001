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

from flask_login import current_user
from .models import RoleEnum, DocumentCategory, EstadoAuditoriaEnum
from .services.nonconformities import ESTADOS_NO_CONFORMIDAD
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, BooleanField, SubmitField, DateField, IntegerField, SelectField
from wtforms.validators import DataRequired, Length, NumberRange, EqualTo, Email, Optional
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
    auditor = StringField('Auditor', validators=[DataRequired(), Length(max=50)])
    resultado = TextAreaField('Resultado', validators=[DataRequired()])
    accion_correctiva = TextAreaField('Acción Correctiva')
    estado = SelectField(
        'Estado',
        choices=[(e.name, e.value) for e in EstadoAuditoriaEnum],
        validators=[DataRequired()]
    )
    submit = SubmitField('Guardar')

    def validate(self, extra_validators=None):
        """
        Validación adicional para asegurar que el usuario tiene permisos para crear/editar auditorías.
        """
        if not super().validate(extra_validators=extra_validators):
            return False
        # Validar que el usuario actual tenga permiso para modificar auditorías
        if current_user.role not in [RoleEnum.AUDITOR, RoleEnum.ADMINISTRADOR]:
            self.area_auditada.errors.append("No tienes permiso para registrar o editar auditorías.")
            return False
        return True



# Formulario para registrar No Conformidades
class NoConformidadForm(BaseForm):
    descripcion = TextAreaField('Descripción', validators=[DataRequired()])
    fecha_detectada = DateField('Fecha Detectada', validators=[DataRequired()])
    responsable = StringField('Responsable', validators=[Length(max=50)])
    estado = SelectField('Estado', choices=[(e, e) for e in ESTADOS_NO_CONFORMIDAD],
                         default='Abierta', validators=[DataRequired()])
    accion_correctiva = TextAreaField('Acción Correctiva')
    submit = SubmitField('Guardar')

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
    personal = StringField('Personal', validators=[DataRequired(), Length(max=100)])
    duracion_horas = IntegerField('Duración en Horas', validators=[Optional(), NumberRange(min=0)])
    evaluacion_final = StringField('Evaluación Final', validators=[Length(max=20)])
    submit = SubmitField('Guardar')

# Formulario para el registro y actualización de documentos
class DocumentForm(BaseForm):
    title = StringField('Título', validators=[DataRequired(), Length(max=150)])
    code = StringField('Código de Identificación', validators=[DataRequired(), Length(max=50)])
    category = SelectField('Categoría', choices=[(cat.name, cat.value) for cat in DocumentCategory], validators=[DataRequired()])
    version = StringField('Versión', validators=[DataRequired(), Length(max=10)])
    issued_date = DateField('Fecha de Emisión', validators=[DataRequired()])
    approved_by = StringField('Aprobado por', validators=[Length(max=100)])
    content = TextAreaField('Contenido', validators=[DataRequired()])
    submit = SubmitField('Guardar Documento')

# Formulario para solicitar recuperación de contraseña
class PasswordResetRequestForm(BaseForm):
    email = StringField('Correo electrónico', validators=[DataRequired(), Email()])
    submit = SubmitField('Enviar enlace de recuperación')

# Formulario para establecer nueva contraseña
class PasswordResetForm(BaseForm):
    password = PasswordField('Nueva contraseña', validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField('Confirmar contraseña', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Restablecer contraseña')
