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
from flask_login import login_user, logout_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash
from ..models import User, RoleEnum
from ..forms import LoginForm, RegisterForm  # Importa también el formulario de registro
from ..extensions import db

bp = Blueprint('auth', __name__)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash('Inicio de sesión exitoso', 'success')
            return redirect(url_for('dashboard.dashboard'))  # Redirigir al dashboard tras el inicio de sesión
        else:
            flash('Credenciales inválidas', 'danger')
            return redirect(url_for('auth.login'))
    return render_template('login.html', form=form)

@bp.route('/register', methods=['GET', 'POST'])
def register():
    # Verificar si ya hay usuarios registrados
    if User.query.count() > 0:
        flash("Ya existe un usuario registrado. La página de registro está deshabilitada.", "info")
        return redirect(url_for('auth.login'))
    
    form = RegisterForm()
    if form.validate_on_submit():
        username = form.username.data
        password = generate_password_hash(form.password.data)
        
        # Asignar rol de ADMINISTRADOR si es el primer usuario
        role = RoleEnum.ADMINISTRADOR if User.query.count() == 0 else RoleEnum.OPERATIVO
        user = User(username=username, password=password, role=role)
        
        # Guardar el nuevo usuario en la base de datos
        db.session.add(user)
        db.session.commit()
        
        flash("Usuario registrado exitosamente.", "success")
        return redirect(url_for('auth.login'))
    
    return render_template('register.html', form=form)

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión', 'info')
    return redirect(url_for('auth.login'))
