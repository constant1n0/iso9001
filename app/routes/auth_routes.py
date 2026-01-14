# Este archivo es parte de "ABSOLUT ISO9001".
#
# "ABSOLUT ISO9001" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ABSOLUT ISO9001" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

from flask import Blueprint, request, redirect, url_for, render_template, flash
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from werkzeug.security import check_password_hash, generate_password_hash
from ..models import User, RoleEnum
from ..forms import LoginForm, RegisterForm, PasswordResetRequestForm, PasswordResetForm
from ..extensions import db, limiter, mail
from ..utils.security_logger import (
    log_login_attempt, log_logout, log_registration,
    log_password_reset_request, log_password_change
)

bp = Blueprint('auth', __name__)

@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            log_login_attempt(username, success=True)
            flash('Inicio de sesión exitoso', 'success')
            return redirect(url_for('dashboard.dashboard'))
        else:
            log_login_attempt(username, success=False, reason='invalid_credentials')
            flash('Credenciales inválidas', 'danger')
            return redirect(url_for('auth.login'))
    return render_template('login.html', form=form)

@bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def register():
    # Verificar si ya hay usuarios registrados
    if User.query.count() > 0:
        flash("Ya existe un usuario registrado. La página de registro está deshabilitada.", "info")
        return redirect(url_for('auth.login'))

    form = RegisterForm()
    if form.validate_on_submit():
        username = form.username.data
        email = form.email.data
        password = generate_password_hash(form.password.data, method='pbkdf2:sha256')

        # Asignar rol de ADMINISTRADOR si es el primer usuario
        role = RoleEnum.ADMINISTRADOR if User.query.count() == 0 else RoleEnum.OPERATIVO
        user = User(username=username, email=email, password=password, role=role)

        # Guardar el nuevo usuario en la base de datos
        db.session.add(user)
        db.session.commit()

        log_registration(username, email, success=True)
        flash("Usuario registrado exitosamente.", "success")
        return redirect(url_for('auth.login'))

    return render_template('register.html', form=form)

@bp.route('/logout')
@login_required
def logout():
    username = current_user.username
    logout_user()
    log_logout(username)
    flash('Has cerrado sesión', 'info')
    return redirect(url_for('auth.login'))


def send_reset_email(user):
    """Envía correo con enlace de recuperación de contraseña."""
    token = user.get_reset_token()
    msg = Message(
        'Recuperación de Contraseña - ABSOLUT ISO9001',
        recipients=[user.email]
    )
    msg.body = f'''Para restablecer tu contraseña, visita el siguiente enlace:

{url_for('auth.reset_password', token=token, _external=True)}

Este enlace expirará en 1 hora.

Si no solicitaste este cambio, ignora este mensaje.
'''
    try:
        mail.send(msg)
        return True
    except Exception:
        return False


@bp.route('/reset_password_request', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def reset_password_request():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.dashboard'))

    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            if send_reset_email(user):
                log_password_reset_request(form.email.data, success=True)
                flash('Se ha enviado un correo con instrucciones para restablecer tu contraseña.', 'info')
            else:
                log_password_reset_request(form.email.data, success=False)
                flash('Error al enviar el correo. Por favor, contacte al administrador.', 'danger')
        else:
            # No revelamos si el email existe o no por seguridad
            flash('Se ha enviado un correo con instrucciones para restablecer tu contraseña.', 'info')
        return redirect(url_for('auth.login'))

    return render_template('reset_password_request.html', form=form)


@bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.dashboard'))

    user = User.verify_reset_token(token)
    if not user:
        flash('El enlace de recuperación es inválido o ha expirado.', 'warning')
        return redirect(url_for('auth.reset_password_request'))

    form = PasswordResetForm()
    if form.validate_on_submit():
        user.password = generate_password_hash(form.password.data, method='pbkdf2:sha256')
        db.session.commit()
        log_password_change(user.username, success=True)
        flash('Tu contraseña ha sido actualizada exitosamente.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', form=form)
