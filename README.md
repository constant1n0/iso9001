**Sistema de Gestión de Calidad ISO9001 con Flask y Celery**

Este sistema de gestión de calidad ha sido desarrollado en Flask. Utiliza Celery y Redis para la gestión de tareas en segundo plano, lo que permite enviar notificaciones periódicas y ejecutar otras tareas sin bloquear el funcionamiento principal de la aplicación.

**Tabla de Contenidos**

- Requisitos Previos
- Instalación
- Configuración de la Base de Datos
- Iniciar la Aplicación
- Mantenimiento y Supervisión
- Seguridad
- Actualizaciones y Despliegue en Producción
-----
**Requisitos Previos**

Antes de comenzar con la instalación, asegúrate de tener los siguientes elementos instalados y configurados:

- **Python 3.x** - Lenguaje de programación para ejecutar el proyecto.
- **Redis** - Para manejar las tareas de Celery en segundo plano.
- **PostgreSQL** - Sistema de gestión de bases de datos para almacenar los datos de la aplicación.
- **Servidor de Correo** (Gmail, etc.) - Para el envío de notificaciones por correo electrónico.
-----
**Instalación**

**1. Clonar el Repositorio**

Clona el repositorio en tu máquina local:

git clone <URL\_DEL\_REPOSITORIO>

cd <NOMBRE\_DEL\_REPOSITORIO>

**2. Crear el Entorno Virtual**

Es importante trabajar en un entorno virtual para aislar las dependencias del proyecto. Puedes crear y activar un entorno virtual con los siguientes comandos:

python3 -m venv venv

source venv/bin/activate

**3. Instalar las Dependencias**

Instala las dependencias requeridas para el proyecto desde el archivo requirements.txt:

pip install -r requirements.txt

**4. Configurar las Variables de Entorno**

Este proyecto utiliza variables de entorno para almacenar configuraciones sensibles como credenciales y claves API. Crea un archivo llamado .env en la raíz del proyecto y define las siguientes variables:

DATABASE\_URI=postgresql://usuario:contraseña@localhost/db\_name

SECRET\_KEY=clave-secreta

MAIL\_USERNAME=tu\_email@example.com

MAIL\_PASSWORD=tu\_contraseña

MAIL\_DEFAULT\_SENDER=tu\_email@example.com

CELERY\_BROKER\_URL=redis://localhost:6379/0

CELERY\_RESULT\_BACKEND=redis://localhost:6379/0

**Nota:** Cambia usuario, contraseña, localhost, y db\_name con los valores correspondientes a tu configuración de PostgreSQL.

Para que las variables de entorno sean cargadas automáticamente, el proyecto utiliza **python-dotenv**, lo que permite que Flask lea las configuraciones directamente desde el archivo .env.

-----
**Configuración de la Base de Datos**

**1. Crear la Base de Datos en PostgreSQL**

Asegúrate de que el servicio PostgreSQL esté en ejecución y luego crea una base de datos para la aplicación.

psql -U usuario

CREATE DATABASE GestionCalidadISO9001;

\q

**2. Generar las Migraciones de la Base de Datos**

Asegúrate de que el entorno virtual esté activado y de que las configuraciones de DATABASE\_URI en .env sean correctas.

Ejecuta el comando para generar migraciones de tus modelos en Flask:

flask db migrate -m "Creación de tablas iniciales"

Esto generará los archivos de migración en la carpeta migrations/, basados en los modelos definidos en el código.

` `**3. Aplicar Migraciones de Base de Datos**

Una vez generadas las migraciones, aplica los cambios en la base de datos:

flask db upgrade

Este comando creará todas las tablas en la base de datos especificada, utilizando las definiciones de los modelos en el proyecto.

**Nota**: Cada vez que realices cambios en los modelos, debes ejecutar flask db migrate y luego flask db upgrade para actualizar la estructura de la base de datos.

**4. Iniciar el Worker de Celery**

Celery requiere que Redis esté en ejecución. Una vez que Redis esté activo, inicia el worker de Celery para manejar tareas en segundo plano:

celery -A celery\_worker.celery worker --loglevel=info

**Consejo:** Para monitorear el funcionamiento de Celery y ver cuándo se ejecutan las tareas, puedes revisar el log que aparece en la consola.

-----
**Mantenimiento y Supervisión**

**Tareas de Mantenimiento con Celery y Flask**

- **Celery**:
  - Asegúrate de que Celery esté en ejecución constantemente para que las tareas en segundo plano, como las notificaciones por correo y los reportes de auditoría, se ejecuten en el tiempo programado.
  - Las tareas programadas deben definirse en el archivo de configuración y activarse mediante celery.conf.beat\_schedule.
- **Dashboard**:
  - Revisa regularmente el dashboard de la aplicación para asegurarte de que muestra datos precisos. Los gráficos y notificaciones deben estar actualizados con la información de la base de datos.
  - En el dashboard podrás ver:
    - Estadísticas generales de auditorías, no conformidades, satisfacción del cliente y capacitaciones.
    - Alertas de auditorías próximas, no conformidades abiertas y capacitaciones cercanas.

**Monitoreo de Logs**

- **Logs de Flask**: Revisa los logs del servidor Flask para detectar cualquier error de la aplicación.
- **Logs de Celery**: Asegúrate de que no haya errores en el worker de Celery, especialmente para verificar que las tareas en segundo plano se ejecuten correctamente.
-----
**Seguridad**

- **Protección de Claves**: Asegúrate de que el archivo .env nunca se suba al repositorio, ya que contiene credenciales sensibles. Está configurado en .gitignore para evitar que se suba accidentalmente.
- **Acceso al Dashboard**: Utiliza login\_required y controles de permisos (role\_required) para restringir el acceso a ciertas rutas y asegurar que solo usuarios autorizados puedan ver datos sensibles.
- **Actualizaciones de Dependencias**: Ejecuta actualizaciones regulares de las dependencias y verifica si hay parches de seguridad disponibles para Flask, Celery, y demás dependencias.
-----
**Actualizaciones y Despliegue en Producción**

**Actualizar Dependencias**

Cuando necesites actualizar las dependencias, asegúrate de probar primero en un entorno de desarrollo. Ejecuta:

pip install -U -r requirements.txt

**Servidor de Producción**

Para desplegar en un entorno de producción, configura un servidor de aplicaciones como **Gunicorn** o **uWSGI** para manejar el tráfico entrante. Puedes configurar Nginx como proxy inverso para manejar las solicitudes entrantes y dirigirlas al servidor de Flask.

1. **Iniciar Gunicorn**:

gunicorn -w 4 -b 0.0.0.0:5000 app:app

1. **Configurar Nginx**:
   1. Define una configuración en Nginx para redirigir el tráfico HTTP al servidor de Flask.
   1. Configura HTTPS utilizando **Certbot** para obtener un certificado SSL gratuito de Let's Encrypt.

**Ejemplo de Configuración para Nginx**

server {

`    `listen 80;

`    `server\_name tu\_dominio.com;

`    `location / {

`        `proxy\_pass http://127.0.0.1:5000;

`        `proxy\_set\_header Host $host;

`        `proxy\_set\_header X-Real-IP $remote\_addr;

`        `proxy\_set\_header X-Forwarded-For $proxy\_add\_x\_forwarded\_for;

`        `proxy\_set\_header X-Forwarded-Proto $scheme;

`    `}

}

**Nota:** No olvides reiniciar Nginx tras modificar la configuración:


iso9001/
├── sql
│   └── deployment.sql
├── run.py
├── requirements.txt
├── migrations
├── flaskapp.wsgi
├── celery_worker.py
├── celery_beat_schedule.py
├── app
│   ├── utils
│   │   ├── reports.py
│   │   ├── notifications.py
│   │   ├── generate_report.py
│   │   ├── error_handlers.py
│   │   ├── decorators.py
│   │   └── __init__.py
│   ├── templates
│   │   ├── satisfaccion_cliente
│   │   │   ├── pdf_template.html
│   │   │   ├── nueva.html
│   │   │   ├── listar.html
│   │   │   └── editar.html
│   │   ├── reportes
│   │   │   └── reporte_mensual.html
│   │   ├── register.html
│   │   ├── partes_interesadas
│   │   │   ├── nueva.html
│   │   │   ├── listar.html
│   │   │   └── editar.html
│   │   ├── no_conformidades
│   │   │   ├── pdf_template.html
│   │   │   ├── nueva.html
│   │   │   ├── listar.html
│   │   │   └── editar.html
│   │   ├── login.html
│   │   ├── index.html
│   │   ├── dashboard
│   │   │   └── dashboard.html
│   │   ├── capacitaciones
│   │   │   ├── pdf_template.html
│   │   │   ├── nueva.html
│   │   │   ├── listar.html
│   │   │   └── editar.html
│   │   ├── base.html
│   │   └── auditorias
│   │       ├── pdf_template.html
│   │       ├── nueva.html
│   │       ├── listar.html
│   │       └── editar.html
│   ├── static
│   ├── schemas.py
│   ├── routes
│   │   ├── satisfaccion_cliente_routes.py
│   │   ├── rol_responsabilidad_routes.py
│   │   ├── riesgo_oportunidad_routes.py
│   │   ├── recurso_capacitacion_routes.py
│   │   ├── proceso_operacion_routes.py
│   │   ├── parte_interesada_routes.py
│   │   ├── no_conformidad_routes.py
│   │   ├── mejora_routes.py
│   │   ├── main_routes.py
│   │   ├── dashboard_routes.py
│   │   ├── capacitacion_routes.py
│   │   ├── auth_routes.py
│   │   ├── auditoria_routes.py
│   │   ├── auditoria_indicador_routes.py
│   │   └── __init__.py
│   ├── models.py
│   ├── forms.py
│   ├── extensions.py
│   ├── config.py
│   └── __init__.py
└── README.md

## Licencia

Este proyecto está licenciado bajo la Licencia Pública General GNU v3.0. Para más detalles, consulta el archivo [LICENSE](./LICENSE).
