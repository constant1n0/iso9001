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

from collections.abc import Mapping

from flask import Flask

from .commands import register_commands
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
from .utils.security_logger import init_security_logging
from celery import Celery


def create_app(test_config: Mapping[str, object] | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

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
    init_security_logging(app)
    register_commands(app)

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
