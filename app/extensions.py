from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
from flask_migrate import Migrate
from flask_caching import Cache
from flask_wtf import CSRFProtect
from flask_login import LoginManager

db = SQLAlchemy()
ma = Marshmallow()
migrate = Migrate()
cache = Cache()
csrf = CSRFProtect()
login_manager = LoginManager()
