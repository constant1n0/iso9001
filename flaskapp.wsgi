import sys
import os

# Añadir el directorio del proyecto al sys.path
project_home = os.path.dirname(os.path.abspath(__file__))
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Importar la función create_app desde el paquete app
from app import create_app

# Crear la instancia de la aplicación
application = create_app()
