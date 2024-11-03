from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_caching import Cache

# Inicialización de extensiones
db = SQLAlchemy()
ma = Marshmallow()
migrate = Migrate()
jwt = JWTManager()
cache = Cache()
