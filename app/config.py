# config.py
import os
from dotenv import load_dotenv
from celery.schedules import crontab

# Cargar las variables del archivo .env
load_dotenv()

class Config:
    # Configuración de la base de datos
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URI')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Clave secreta para sesiones y CSRF
    SECRET_KEY = os.environ.get('SECRET_KEY')

    # Configuración de Caché
    CACHE_TYPE = 'simple'
    CACHE_DEFAULT_TIMEOUT = 300

    # Configuración de correo
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 587
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')

    # Configuración para Celery
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL')
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND')

    # Programación de tareas periódicas de Celery
    CELERYBEAT_SCHEDULE = {
        'enviar-alerta-auditorias-proximas': {
            'task': 'enviar_alerta_auditorias_proximas',
            'schedule': crontab(hour=7, minute=0),  # Corre la tarea todos los días a las 7:00 AM
        },
    }
