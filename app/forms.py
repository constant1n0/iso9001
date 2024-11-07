from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, TextAreaField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Length

# Formulario de Inicio de Sesión
class LoginForm(FlaskForm):
    username = StringField('Usuario', validators=[DataRequired(), Length(min=4, max=150)])
    password = PasswordField('Contraseña', validators=[DataRequired(), Length(min=6)])
    submit = SubmitField('Iniciar Sesión')

# Formulario para ParteInteresada
class ParteInteresadaForm(FlaskForm):
    nombre = StringField('Nombre', validators=[DataRequired(), Length(max=50)])
    necesidades_expectativas = TextAreaField('Necesidades y Expectativas')
    requisitos_identificados = TextAreaField('Requisitos Identificados')
    objetivo_estrategico = TextAreaField('Objetivo Estratégico')
    submit = SubmitField('Guardar')

# Puedes definir formularios similares para las otras entidades
