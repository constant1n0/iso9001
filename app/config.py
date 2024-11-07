import os

class Config:
    # Configuración de la base de datos
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URI', 'postgresql://usuario:contraseña@localhost/db_name')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Clave secreta para sesiones y CSRF
    SECRET_KEY = os.environ.get('SECRET_KEY', 'una-clave-secreta-muy-segura')

    # Configuración de Caché
    CACHE_TYPE = 'simple'
    CACHE_DEFAULT_TIMEOUT = 300
