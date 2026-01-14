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

from flask import Flask, redirect, url_for, request
from .config import Config
from .extensions import db, ma, migrate, cache, csrf, login_manager, mail, limiter
from .models import User
from .routes import (
    auth_routes,
    main_routes,
    parte_interesada_routes,
    riesgo_oportunidad_routes,
    rol_responsabilidad_routes,
    recurso_capacitacion_routes,
    proceso_operacion_routes,
    auditoria_indicador_routes,
    mejora_routes,
    dashboard_routes,
    auditoria_routes,
    no_conformidad_routes,
    capacitacion_routes,
    satisfaccion_cliente_routes,
    document_routes
)
from .utils.error_handlers import register_error_handlers
from celery import Celery

# Variable para cachear si hay usuarios (evita queries en cada request)
_has_users = None


def create_app():
    global _has_users
    app = Flask(__name__)
    app.config.from_object(Config)

    # Validar configuración crítica
    if not app.config.get('SECRET_KEY'):
        raise RuntimeError("SECRET_KEY no está configurada. Configure la variable de entorno SECRET_KEY.")

    if not app.config.get('SQLALCHEMY_DATABASE_URI'):
        raise RuntimeError("DATABASE_URI no está configurada. Configure la variable de entorno DATABASE_URI.")

    # Inicializar extensiones
    db.init_app(app)
    ma.init_app(app)
    migrate.init_app(app, db)
    cache.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)

    # Configurar la vista de inicio de sesión
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Por favor, inicia sesión para acceder a esta página.'
    login_manager.login_message_category = 'warning'

    # Cargar el usuario desde la base de datos (usando Session.get() recomendado en SQLAlchemy 2.0)
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Registrar Blueprints
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(main_routes.bp)
    app.register_blueprint(parte_interesada_routes.bp)
    app.register_blueprint(riesgo_oportunidad_routes.bp)
    app.register_blueprint(rol_responsabilidad_routes.bp)
    app.register_blueprint(recurso_capacitacion_routes.bp)
    app.register_blueprint(proceso_operacion_routes.bp)
    app.register_blueprint(auditoria_indicador_routes.bp)
    app.register_blueprint(mejora_routes.bp)
    app.register_blueprint(dashboard_routes.bp)
    app.register_blueprint(auditoria_routes.bp)
    app.register_blueprint(no_conformidad_routes.bp)
    app.register_blueprint(capacitacion_routes.bp)
    app.register_blueprint(satisfaccion_cliente_routes.bp)
    app.register_blueprint(document_routes.bp)

    # Registrar manejadores de errores
    register_error_handlers(app)

    # Configuración de Celery (solo si está configurado)
    if app.config.get('CELERY_BROKER_URL'):
        app.celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'])
        app.celery.conf.update(app.config)

    # Verificar si la tabla de usuarios está vacía y redirigir al formulario de registro
    # Optimizado para evitar consultas innecesarias
    @app.before_request
    def check_for_empty_users():
        global _has_users

        # Excluir archivos estáticos y endpoints específicos
        if request.endpoint and (
            request.endpoint.startswith('static') or
            request.endpoint == 'auth.register' or
            request.endpoint == 'auth.login'
        ):
            return None

        # Si ya sabemos que hay usuarios, no hacer más queries
        if _has_users:
            return None

        # Verificar si hay usuarios (consulta optimizada)
        try:
            if db.session.query(User.id).first() is None:
                return redirect(url_for('auth.register'))
            else:
                _has_users = True
        except Exception:
            # Si hay error de BD (ej: tablas no creadas), permitir continuar
            pass

        return None

    # Resetear cache cuando se registra un usuario
    @app.after_request
    def after_register(response):
        global _has_users
        if request.endpoint == 'auth.register' and request.method == 'POST' and response.status_code in [200, 302]:
            _has_users = True
        return response

    # Headers de seguridad HTTP
    @app.after_request
    def add_security_headers(response):
        # Prevenir clickjacking
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        # Prevenir MIME type sniffing
        response.headers['X-Content-Type-Options'] = 'nosniff'
        # Habilitar filtro XSS del navegador
        response.headers['X-XSS-Protection'] = '1; mode=block'
        # Política de referencia
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        # Permisos del navegador
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        # Content Security Policy
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'self';"
        )
        # HSTS (solo en producción con HTTPS)
        if not app.config.get('DEBUG', False):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    return app
