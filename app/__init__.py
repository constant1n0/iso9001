# app/__init__.py

from flask import Flask
from .config import Config
from .extensions import db, ma, migrate, cache, csrf, login_manager
from .models import User  # Asegúrate de que el modelo User está definido
from .routes import (
    auth_routes,
    main_routes,  # Nuevo Blueprint para rutas principales
    parte_interesada_routes,
    riesgo_oportunidad_routes,
    rol_responsabilidad_routes,
    recurso_capacitacion_routes,
    proceso_operacion_routes,
    auditoria_indicador_routes,
    mejora_routes
)
from .utils.error_handlers import handle_exception

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Inicializar extensiones
    db.init_app(app)
    ma.init_app(app)
    migrate.init_app(app, db)
    cache.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    # Configurar la vista de inicio de sesión
    login_manager.login_view = 'auth.login'

    # Cargar el usuario desde la base de datos
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

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

    # Registrar manejadores de errores
    app.register_error_handler(Exception, handle_exception)

    return app
