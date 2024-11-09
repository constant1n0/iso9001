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

from flask_sqlalchemy import SQLAlchemy
from flask_marshmallow import Marshmallow
from flask_migrate import Migrate
from flask_caching import Cache
from flask_wtf import CSRFProtect
from flask_login import LoginManager
from flask_mail import Mail

db = SQLAlchemy()
ma = Marshmallow()
migrate = Migrate()
cache = Cache()
csrf = CSRFProtect()
login_manager = LoginManager()
mail = Mail()

def init_app(app):
    mail.init_app(app)
