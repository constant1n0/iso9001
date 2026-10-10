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

import os
from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask

from .commands import register_commands
from .config import Config
from .extensions import db, ma, migrate, csrf, login_manager, mail, limiter
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
    corrective_action_routes,
    capacitacion_routes,
    satisfaccion_cliente_routes,
    document_routes,
    user_routes,
    profile_routes,
    people_routes,
    competence_routes,
)
from .utils.error_handlers import register_error_handlers
from .utils.permissions import can
from .utils.security_logger import init_security_logging
from .utils.trusted_proxies import TrustedProxyMiddleware, parse_trusted_proxies

# Room for the form fields and multipart framing around one attachment.
MULTIPART_ALLOWANCE = 1024 * 1024


def configure_document_storage(app: Flask) -> None:
    """Resolve the attachment directory and limits (decision DC7); create the directory.

    ``DOCUMENT_STORAGE_DIR`` defaults to ``<instance path>/documents`` and
    never lies under ``static/``; ``DOCUMENT_MAX_BYTES`` must be a positive
    number of bytes. Unless set explicitly, ``MAX_CONTENT_LENGTH`` is that
    limit plus ``MULTIPART_ALLOWANCE``, so a larger request gets a clean 413.
    """
    raw = app.config.get('DOCUMENT_MAX_BYTES')
    try:
        max_bytes = 0 if isinstance(raw, bool) else int(raw)
    except (TypeError, ValueError):
        max_bytes = 0
    if max_bytes <= 0:
        raise RuntimeError("DOCUMENT_MAX_BYTES debe ser un número positivo de bytes.")
    app.config['DOCUMENT_MAX_BYTES'] = max_bytes
    if app.config.get('MAX_CONTENT_LENGTH') is None:
        app.config['MAX_CONTENT_LENGTH'] = max_bytes + MULTIPART_ALLOWANCE

    configured = app.config.get('DOCUMENT_STORAGE_DIR')
    directory = os.path.realpath(configured or os.path.join(app.instance_path, 'documents'))
    static = os.path.realpath(app.static_folder) if app.static_folder else None
    if static is not None and os.path.commonpath([directory, static]) == static:
        raise RuntimeError("DOCUMENT_STORAGE_DIR no puede estar dentro de static/.")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    app.config['DOCUMENT_STORAGE_DIR'] = directory


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

    # Attachments: storage directory and request size limit (DC7)
    configure_document_storage(app)

    # Real client behind trusted proxies; '*' or an invalid entry stops start-up
    trusted_proxies = parse_trusted_proxies(app.config.get('TRUSTED_PROXIES'))
    if trusted_proxies:
        app.wsgi_app = TrustedProxyMiddleware(app.wsgi_app, trusted_proxies)

    # Inicializar extensiones
    db.init_app(app)
    ma.init_app(app)
    migrate.init_app(app, db)
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
        # An inactive user loses its session and remember cookie on the next request.
        user = db.session.get(User, int(user_id))
        return user if user is not None and user.is_active else None

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
    app.register_blueprint(corrective_action_routes.bp)
    app.register_blueprint(capacitacion_routes.bp)
    app.register_blueprint(satisfaccion_cliente_routes.bp)
    app.register_blueprint(document_routes.bp)
    app.register_blueprint(user_routes.bp)
    app.register_blueprint(profile_routes.bp)
    app.register_blueprint(people_routes.bp)
    app.register_blueprint(competence_routes.bp)

    # Registrar manejadores de errores
    register_error_handlers(app)

    # ``can(action, resource)`` in templates follows the same policy as the services.
    app.add_template_global(can)

    @app.context_processor
    def inject_current_year():
        # Footer copyright year, in the application's timezone.
        return {'current_year': datetime.now(ZoneInfo(app.config['APP_TIMEZONE'])).year}

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
            # No inline scripts, handlers or styles (enforced by
            # tests/test_ui_cierre.py), so 'unsafe-inline' is not needed.
            "script-src 'self'; "
            "style-src 'self'; "
            "font-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'self';"
        )
        # HSTS (solo en producción con HTTPS)
        if not app.config.get('DEBUG', False):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    return app
