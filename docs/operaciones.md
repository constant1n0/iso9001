# Guía de operaciones

Cómo instalar, configurar, ejecutar, desplegar, respaldar y monitorizar el SGC
ISO 9001. Cada orden y cada variable de esta guía proceden del código en el
momento de su redacción: `app/config.py`, `app/__init__.py`, `app/commands.py`,
`celery_worker.py`, `gunicorn.conf.py`, las unidades de systemd de la raíz del
repositorio y `.github/workflows/ci.yml`. El servidor MCP tiene su propia guía,
[`mcp.md`](mcp.md); la capa de servicios se describe en
[`architecture/services.md`](architecture/services.md) (en inglés).

Las órdenes se ejecutan desde la raíz de la copia de trabajo, con el entorno
virtual en `venv/`. Los marcadores de posición se escriben como `<like-this>`;
nunca ponga contraseñas, tokens ni nombres de host reales en ficheros sujetos
al control de versiones.

## Contenido

- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [PostgreSQL y migraciones](#postgresql-y-migraciones)
- [Redis y Celery](#redis-y-celery)
- [Usuarios y el primer administrador](#usuarios-y-el-primer-administrador)
- [Tokens de API](#tokens-de-api)
- [Ficheros adjuntos de los documentos](#ficheros-adjuntos-de-los-documentos)
- [Ejecución en desarrollo](#ejecución-en-desarrollo)
- [Ejecución en producción](#ejecución-en-producción)
- [Procedimiento de despliegue](#procedimiento-de-despliegue)
- [Copias de seguridad y restauración](#copias-de-seguridad-y-restauración)
- [Monitorización y registros](#monitorización-y-registros)
- [Notas de seguridad](#notas-de-seguridad)
- [Integración continua](#integración-continua)

## Requisitos

- **Python 3.11**, la versión con la que se prueba en la integración continua
  (`requirements.txt` fija la versión exacta de cada paquete).
- **PostgreSQL**. La integración continua y producción usan PostgreSQL 17. Las
  migraciones usan DDL exclusivo de PostgreSQL, por lo que no se admite ninguna
  otra base de datos.
- **Redis**, para el intermediario (*broker*) de Celery y, en producción, para
  los contadores de los límites de peticiones. La aplicación web funciona sin
  él.
- **Una cuenta SMTP** para el correo electrónico: los enlaces de
  restablecimiento de contraseña y las notificaciones programadas. El servidor
  de correo está fijado en el código (véase
  [Ajustes fijados en el código](#ajustes-fijados-en-el-código)).
- **Las bibliotecas del sistema que usa WeasyPrint** para los PDF. En Ubuntu,
  la integración continua instala:

  ```bash
  sudo apt-get install --no-install-recommends \
    libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libharfbuzz-subset0
  ```

Redis puede instalarse desde la distribución (`sudo apt install redis-server`
en Ubuntu o Debian; `brew install redis` y `brew services start redis` en
macOS) o ejecutarse en un contenedor.

## Instalación

```bash
git clone https://github.com/constant1n0/iso9001.git
cd iso9001
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/pip check
```

A continuación, escriba `.env` en la raíz de la copia de trabajo (véase
[Configuración](#configuración)), cree la base de datos y aplique las
migraciones (véase [PostgreSQL y migraciones](#postgresql-y-migraciones)) y
cree el primer administrador (véase
[Usuarios y el primer administrador](#usuarios-y-el-primer-administrador)).

## Configuración

La aplicación lee su configuración de variables de entorno. `app/config.py`
llama a `python-dotenv`, de modo que la aplicación web, las órdenes de la
CLI, Celery y el servidor MCP cargan por igual un fichero `.env`
situado en la raíz de la copia de trabajo; las unidades de systemd también lo
pasan como `EnvironmentFile`. `.env` figura en `.gitignore`: manténgalo fuera
del control de versiones y legible solo por el usuario del servicio (modo
`600`). El fichero versionado `.env.example` es un punto de partida;
contrástelo con las tablas siguientes, que proceden del código.

### Variables de entorno

| Variable | Valor por defecto | Significado |
|---|---|---|
| `SECRET_KEY` | ninguno (obligatoria) | Firma las sesiones, los tokens CSRF y los enlaces de restablecimiento de contraseña, y es la clave de los hashes de los tokens de API. La aplicación no arranca sin ella. Cambiarla cierra la sesión de todos los usuarios e invalida todos los tokens de API |
| `DATABASE_URI` | ninguno (obligatoria) | URL de SQLAlchemy de la base de datos PostgreSQL, por ejemplo `postgresql://<user>:<password>@<host>:5432/<database>`. La aplicación no arranca sin ella |
| `FLASK_DEBUG` | `False` | `true`, `1` o `yes` activa el modo de depuración. En modo de depuración, las cookies de sesión y de «recordarme» no se marcan como `Secure` y no se envía HSTS. No la active nunca en producción |
| `APP_TIMEZONE` | `Europe/Madrid` | Zona horaria de «hoy» (fechas de aprobación, publicación y cierre, comprobación de vencimientos, periodos de los informes) y del calendario de Celery |
| `MAIL_USERNAME` | ninguno | Usuario de SMTP |
| `MAIL_PASSWORD` | ninguno | Contraseña de SMTP |
| `MAIL_DEFAULT_SENDER` | ninguno | Dirección del remitente de todos los correos |
| `PASSWORD_RESET_BASE_URL` | ninguno | Origen HTTPS raíz con el que se construyen los enlaces de restablecimiento de contraseña, por ejemplo `https://qms.example.com`. Véase [Enlaces de restablecimiento de contraseña](#enlaces-de-restablecimiento-de-contraseña) |
| `TRUSTED_PROXIES` | vacío | Proxies inversos cuyas cabeceras `X-Forwarded-For` y `X-Forwarded-Proto` se respetan: direcciones o redes CIDR separadas por comas. Vacío significa ninguno. `*` o una entrada no válida detienen el arranque. Véase [Proxy inverso](#proxy-inverso) |
| `RATELIMIT_STORAGE_URI` | `memory://` | Almacenamiento de los contadores de los límites de peticiones, compartido por la aplicación web y el servidor MCP. En producción, use Redis, por ejemplo `redis://:<password>@127.0.0.1:6379/2`, en un índice de base de datos distinto del de Celery |
| `MCP_TOKEN_RATE_LIMIT` | `120/minute` | Transporte HTTP de MCP: peticiones por token de API. Véase [`mcp.md`](mcp.md#rate-limits) |
| `MCP_AUTH_FAILURE_RATE_LIMIT` | `20/minute` | Transporte HTTP de MCP: autenticaciones *bearer* fallidas por dirección de cliente |
| `DOCUMENT_STORAGE_DIR` | `instance/documents` | Directorio de los ficheros adjuntos de las revisiones de documentos (`<instance path>/documents`; la ruta de instancia es `instance/` en la copia de trabajo). Se crea al arrancar con modo `700`; el arranque falla si está dentro de `app/static/`. Véase [Ficheros adjuntos de los documentos](#ficheros-adjuntos-de-los-documentos) |
| `DOCUMENT_MAX_BYTES` | `20971520` (20 MiB) | Tamaño máximo de un adjunto, en bytes. Debe ser un entero positivo o el arranque falla. Las peticiones que superan este valor más 1 MiB para el formulario reciben `413` |
| `SECURITY_LOG_ENABLED` | `True` | `true`, `1` o `yes` escribe el registro de seguridad; cualquier otro valor lo desactiva |
| `SECURITY_LOG_FILE` | `logs/security.log` | Ruta del registro de seguridad (valor relativo por defecto: `logs/` en la raíz de la copia de trabajo, que se crea cuando hace falta) |
| `CELERY_BROKER_URL` | ninguno | Intermediario de Celery, por ejemplo `redis://:<password>@127.0.0.1:6379/0`. Lo necesitan el *worker* y el *beat* de Celery, que no arrancan sin él; la aplicación web no lo usa |
| `CELERY_RESULT_BACKEND` | ninguno | Opcional. Las tareas ignoran sus resultados, así que puede quedar sin definir |
| `GUNICORN_BIND` | `0.0.0.0:5000` | La lee `gunicorn.conf.py`: dirección y puerto en los que escucha Gunicorn |
| `GUNICORN_WORKERS` | `2 × CPUs + 1` | La lee `gunicorn.conf.py`: número de procesos *worker* |
| `GUNICORN_THREADS` | `2` | La lee `gunicorn.conf.py`: hilos por *worker* |

El servidor MCP (`python -m app.mcp_server`) también lee `MCP_HOST` (por
defecto `127.0.0.1`), `MCP_PORT` (`8765`), `MCP_ALLOWED_HOSTS`,
`MCP_TRUSTED_PROXIES` (`127.0.0.1`) y, solo para el transporte stdio,
`ISO9001_MCP_TOKEN`; su significado está en
[`mcp.md`](mcp.md#server-configuration).

Las pruebas leen `TEST_POSTGRES_URI` (véase
[Integración continua](#integración-continua)).

`FLASK_APP` no hace falta: las órdenes de esta guía pasan `--app run.py`.
Flask 3 ya no lee `FLASK_ENV`; use `FLASK_DEBUG`.

### Ajustes fijados en el código

Se definen en `app/config.py`, no en el entorno:

- Correo: `MAIL_SERVER = smtp.gmail.com`, `MAIL_PORT = 587`, `MAIL_USE_TLS = True`.
  Otro proveedor de SMTP requiere un cambio en el código.
- La cookie de sesión es `HttpOnly`, `SameSite=Lax` y `Secure` salvo que
  `FLASK_DEBUG` esté activado. Hay configurados una duración de sesión
  permanente (8 horas) y unos ajustes de «recordarme» (7 días, con los mismos
  atributos), pero el inicio de sesión no crea ni una sesión permanente ni una
  cookie de «recordarme», de modo que la sesión termina al cerrar el
  navegador.
- Las claves de los límites de peticiones usan el prefijo `iso9001`; un
  almacenamiento Redis recibe tiempos de espera de conexión y de socket de 1
  segundo, y mientras no está accesible lo sustituyen contadores en memoria.

## PostgreSQL y migraciones

Cree un rol y una base de datos para la aplicación y, después, aplique las
migraciones incluidas en `migrations/` (Flask-Migrate sobre Alembic):

```bash
psql -U postgres -c "CREATE ROLE <user> LOGIN PASSWORD '<password>';"
psql -U postgres -c "CREATE DATABASE <database> OWNER <user>;"
venv/bin/flask --app run.py db upgrade
venv/bin/flask --app run.py db check     # the models match the migrated schema
venv/bin/flask --app run.py db current   # the revision the database is at
```

`flask db upgrade` crea todas las tablas en una base de datos vacía y lleva una
existente a la revisión más reciente. No ejecute nunca `flask db init`: el
directorio `migrations/` ya existe. Un cambio en los modelos se entrega con su
propia migración, que se genera con `flask db migrate -m "<message>"` contra
una base de datos actualizada y se revisa a mano; `tests/test_migrations.py`
falla si el esquema migrado difiere de los modelos.

`sql/deployment.sql` es un script de esquema heredado, anterior a las
migraciones. No se usa y no coincide con los modelos.

## Redis y Celery

Celery envía cuatro correos programados. La aplicación web funciona sin él.
El calendario es `celery.conf.beat_schedule` en `celery_worker.py`; las horas
son las locales de `APP_TIMEZONE`.

| Entrada de beat | Tarea | Destinatarios | Cuándo |
|---|---|---|---|
| `upcoming-audits-alert-daily` | `iso9001.send_upcoming_audits_alert` | Usuarios con el rol Auditor | Todos los días a las 07:00 |
| `pending-audits-report-weekly` | `iso9001.send_pending_audits_report` | Usuarios con el rol Administrador | Los lunes a las 08:00 |
| `monthly-quality-report` | `iso9001.send_monthly_quality_report` | Usuarios con el rol Administrador | El día 1 de cada mes a las 08:00, con un PDF del mes terminado |
| `document-review-alert-weekly` | `iso9001.send_document_review_alert` | Propietarios de documentos (a través del usuario vinculado a su persona) y administradores | Los lunes a las 08:00 |

- La alerta diaria enumera las auditorías pendientes o en proceso de los
  próximos 7 días; el informe semanal enumera las auditorías pendientes; la
  alerta de revisión de documentos enumera los documentos en uso cuya fecha de
  próxima revisión ha llegado. El contenido de cada mensaje se describe en la
  [guía de usuario](guia-de-usuario.md#notificaciones-por-correo-electrónico).
- Cada destinatario recibe un mensaje. Los usuarios inactivos y los usuarios
  sin dirección de correo electrónico se omiten (y queda constancia en el
  registro), igual que los propietarios de documentos sin usuario vinculado.
  Si un envío falla, la tarea lo registra, continúa con los demás
  destinatarios y termina con error.

Ejecute el *worker* y exactamente un *beat* con el mismo entorno que la
aplicación web:

```bash
venv/bin/celery -A celery_worker.celery worker --loglevel=info
venv/bin/celery -A celery_worker.celery beat --loglevel=info
```

Ambos necesitan `CELERY_BROKER_URL` y los ajustes de correo. Beat guarda su
estado en un fichero `celerybeat-schedule` (ignorado por git); la unidad de
producción pasa `--schedule=<checkout>/celerybeat-schedule`. Para enviar una
notificación a mano mientras el *worker* está en marcha:

```bash
venv/bin/python -c "import celery_worker as w; w.send_upcoming_audits_alert.delay()"
```

## Usuarios y el primer administrador

No existe el registro público. Cree el primer administrador desde una consola
de confianza en el servidor; la contraseña solo se teclea cuando se solicita,
sin mostrarse en pantalla:

```bash
venv/bin/flask --app run.py create-admin
```

- Pide un nombre de usuario (de 4 a 150 caracteres), una dirección de correo
  electrónico y una contraseña de al menos 8 caracteres, que se escribe dos
  veces.
- Se niega a ejecutarse en cuanto existe alguna cuenta y nunca modifica las
  cuentas existentes. Ejecútela una sola vez: vuelve a comprobarlo antes de
  confirmar la transacción, pero dos ejecuciones simultáneas no se serializan.

Las demás cuentas las gestionan los administradores en la web
(*Administración › Usuarios*, `/usuarios/`): crear usuarios, cambiar su correo
electrónico y su rol, desactivarlos y reactivarlos, y enviar enlaces de
restablecimiento de contraseña. Véase la
[guía de usuario](guia-de-usuario.md#usuarios).

## Tokens de API

Los tokens de API permiten que un agente actúe como un usuario a través del
servidor MCP, con el rol de ese usuario acotado por los ámbitos del token
(`read`, `write`). Solo se emiten desde una consola de confianza en el
servidor:

```bash
venv/bin/flask --app run.py create-api-token --user <username-or-email> --name "Claude Code"
venv/bin/flask --app run.py create-api-token --user <username-or-email> --name "Bot" --scope read --scope write --days 30
venv/bin/flask --app run.py list-api-tokens [--user <username-or-email>]
venv/bin/flask --app run.py revoke-api-token <prefix>
```

- `create-api-token` usa por defecto el ámbito `read` y 90 días; `--days`
  admite de 1 a 365. Imprime el token (`iso_<prefix>_<secret>`) una sola vez.
  Solo se guarda un hash con clave, de modo que un token perdido no puede
  recuperarse y debe sustituirse.
- Cambiar `SECRET_KEY` invalida todos los tokens.
- `list-api-tokens` muestra el ID, el prefijo, el propietario, el nombre, los
  ámbitos, el estado, la caducidad y el último uso, nunca un secreto.
- `revoke-api-token` recibe el prefijo de 8 caracteres.
- La emisión y la revocación se escriben en el registro de auditoría y en el
  registro de seguridad. Las órdenes actúan como un administrador a través del
  canal `cli`, identificadas con la cuenta del sistema operativo.

Los usuarios enumeran y revocan sus propios tokens en su página de perfil
(`/perfil/`); desactivar un usuario revoca sus tokens activos. La
configuración de los clientes, el punto de acceso HTTP y sus límites están en
[`mcp.md`](mcp.md#issue-a-token); cómo se autentica un adaptador, en
[`architecture/services.md`](architecture/services.md#api-tokens) (en inglés).

## Ficheros adjuntos de los documentos

Cada revisión de un documento puede llevar un fichero (PDF, DOCX, XLSX u ODT,
comprobado por su contenido además de por su extensión).

- **Almacenamiento**: los ficheros se guardan en `DOCUMENT_STORAGE_DIR` (por
  defecto `instance/documents`) con nombres aleatorios; el nombre original, el
  tamaño, el SHA-256 y el tipo se guardan en la base de datos. Solo se sirven
  a través de la ruta de descarga autorizada, nunca desde `static/`.
- **Tamaño**: `DOCUMENT_MAX_BYTES` (por defecto 20 MiB). Un proxy inverso
  situado delante debe aceptar cuerpos de al menos ese tamaño más 1 MiB (en
  Nginx, `client_max_body_size 21m;`).
- **Copias de seguridad**: la base de datos solo registra qué fichero
  corresponde a cada revisión. Respalde el directorio junto con el volcado de
  la base de datos y restaure ambos a la vez (véase
  [Copias de seguridad y restauración](#copias-de-seguridad-y-restauración)).
- **Ubicación en producción**: las unidades de systemd se ejecutan con
  `ProtectSystem=strict` y con permiso de escritura solo en la copia de
  trabajo (`ReadWritePaths`). El directorio por defecto, dentro de la copia de
  trabajo, funciona tal cual; un directorio situado en otro lugar debe
  añadirse a `ReadWritePaths` de la unidad web, y debe persistir entre
  despliegues.
- **Limpieza**: un fichero sustituido o eliminado se borra después de guardar
  el cambio. Si ese borrado falla, o si la base de datos se restaura a un
  punto anterior, pueden quedar ficheros sin referencia. Elimínelos con:

  ```bash
  venv/bin/flask --app run.py cleanup-document-files --dry-run   # report only
  venv/bin/flask --app run.py cleanup-document-files
  ```

  La orden solo lee la base de datos. Conserva los ficheros modificados en la
  última hora (puede que una subida aún se esté guardando) y todo lo que no
  sea un fichero almacenado.

Las subidas, las eliminaciones, los ficheros rechazados y los borradores
descartados se escriben en el registro de seguridad con el usuario, el
documento, la revisión, el nombre del fichero, el tamaño y el SHA-256.

## Ejecución en desarrollo

Use un PostgreSQL desechable, por ejemplo en un contenedor:

```bash
docker run -d --name iso9001-dev-db -e POSTGRES_PASSWORD=<password> \
  -p 127.0.0.1:5432:5432 postgres:17-alpine
```

Un `.env` mínimo para desarrollo:

```bash
SECRET_KEY=<random-string>
DATABASE_URI=postgresql://postgres:<password>@127.0.0.1:5432/postgres
FLASK_DEBUG=1
```

Después, aplique las migraciones, cree el administrador y arranque el servidor
de desarrollo:

```bash
venv/bin/flask --app run.py db upgrade
venv/bin/flask --app run.py create-admin
venv/bin/flask --app run.py run
```

Abra `http://127.0.0.1:5000`, inicie sesión y compruebe que el panel de
control (`/dashboard/`) muestra sus tarjetas y gráficos. Sin
`CELERY_BROKER_URL` no se envía ningún correo programado; sin los ajustes de
correo tampoco se envían los correos de restablecimiento de contraseña.
Ejecute las pruebas con:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

## Ejecución en producción

En producción se ejecutan cuatro servicios de systemd desde una única copia de
trabajo y un único `.env`: Gunicorn para la aplicación web, el *worker* de
Celery, el *beat* de Celery y el servidor MCP. Un proxy inverso termina TLS
delante de Gunicorn y del servidor MCP.

### Gunicorn

```bash
venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
```

`wsgi.py` crea la aplicación; `gunicorn.conf.py` establece:

- la dirección de escucha, los *workers* y los hilos a partir de
  `GUNICORN_BIND`, `GUNICORN_WORKERS` y `GUNICORN_THREADS`. Escuche en
  `127.0.0.1` (o en la dirección por la que llega el proxy) en lugar de en el
  valor por defecto, `0.0.0.0`;
- un tiempo de espera de 120 segundos, el reciclado de los *workers* tras 1000
  peticiones (con una variación aleatoria) y límites para la línea de petición
  y las cabeceras;
- `preload_app = True`: una recarga con `HUP` no carga el código nuevo, así
  que reinicie el servicio después de un despliegue;
- el registro de acceso, el registro de errores y el fichero PID en rutas
  absolutas, dentro del directorio `logs/` y en la raíz de la copia de trabajo
  de producción; cámbielas para otra organización de directorios;
- un formato de registro de acceso sin la ruta de la petición, la cadena de
  consulta ni el Referer (véase
  [Monitorización y registros](#monitorización-y-registros)).

`gunicorn.conf.py` no establece `forwarded_allow_ips`, de modo que Gunicorn
solo confía en las cabeceras de proxy que llegan desde loopback (su valor por
defecto); es `TRUSTED_PROXIES`, en la aplicación, lo que decide la dirección y
el esquema del cliente.

`flaskapp.wsgi` expone la aplicación como `application` para un servidor de
tipo mod_wsgi; las unidades de systemd no lo usan.

### Proxy inverso

El servidor de producción usa Traefik en la misma máquina: termina TLS y
encamina el host del SGC a Gunicorn y `/mcp` al servidor MCP (véase
[`mcp.md`](mcp.md#traefik-routing)). Cualquier proxy debe:

- terminar TLS: las cookies seguras, HSTS y los enlaces de restablecimiento
  presuponen HTTPS;
- conservar la cabecera `Host` original, enviar `X-Forwarded-For` y
  `X-Forwarded-Proto` y figurar en `TRUSTED_PROXIES` (para Traefik en Docker,
  su red, por ejemplo `172.18.0.0/16`; para un proxy en el mismo servidor,
  `127.0.0.1`). De lo contrario, todos los usuarios comparten la dirección del
  proxy a efectos de los límites de peticiones y del registro de seguridad;
- aceptar cuerpos de petición de `DOCUMENT_MAX_BYTES` más 1 MiB;
- no registrar las rutas de las peticiones en su registro de acceso, porque
  los enlaces de restablecimiento de contraseña llevan su token en la ruta. En
  el servidor de producción, el registro de acceso está desactivado para la
  ruta del SGC.

Un sitio de Nginx equivalente, con HTTPS de Let's Encrypt (por ejemplo,
mediante Certbot):

```nginx
server {
    listen 443 ssl;
    server_name <qms-host>;
    # ssl_certificate and ssl_certificate_key: managed by Certbot

    client_max_body_size 21m;
    access_log off;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Con este sitio, defina `TRUSTED_PROXIES=127.0.0.1` y recargue Nginx después de
cada cambio.

### Unidades de systemd

La raíz del repositorio contiene las cuatro unidades:

| Unidad | Ejecuta |
|---|---|
| `iso9001.service` | `gunicorn -c gunicorn.conf.py wsgi:app` (`Type=notify`; `reload` envía `HUP`) |
| `iso9001-celery-worker.service` | `celery -A celery_worker.celery worker --loglevel=info --concurrency=1` |
| `iso9001-celery-beat.service` | `celery -A celery_worker.celery beat --loglevel=info --schedule=<checkout>/celerybeat-schedule` |
| `iso9001-mcp.service` | `python -m app.mcp_server --transport http --host 127.0.0.1 --port 8765` |

Comparten el mismo usuario y grupo, `WorkingDirectory` (la copia de trabajo),
el `PATH` del entorno virtual, `EnvironmentFile` (el `.env` de la copia de
trabajo), `Restart=on-failure` y el endurecimiento: `PrivateTmp`,
`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only` y permiso
de escritura solo en la copia de trabajo. Contienen la ruta de la copia de
trabajo y la cuenta de producción: para otra organización de directorios,
edite `User`, `Group`, `WorkingDirectory`, `PATH`, `EnvironmentFile`,
`ExecStart` y `ReadWritePaths` y, después, instálelas:

```bash
sudo cp iso9001*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iso9001 iso9001-celery-worker iso9001-celery-beat iso9001-mcp
```

Si PostgreSQL o Redis se ejecutan en contenedores, ordene las unidades después
de `docker.service` (las unidades de Celery ya lo hacen). La unidad MCP escucha
en loopback; un proxy en un contenedor llega a ella mediante un *drop-in* de
systemd que cambia `--host`, más una regla de cortafuegos limitada a la red
del proxy (véase [`mcp.md`](mcp.md#server-configuration)).

## Procedimiento de despliegue

Es el procedimiento que se sigue en cada versión en el servidor de producción.
El servidor de producción no descarga nada de GitHub; el agente o el operador
que despliega no tiene `sudo`, y un operador con `sudo` reinicia los
servicios. Marcadores de posición: `<old>` es el commit que se está
ejecutando y `<new>`, el commit que se despliega.

1. **Comprobación previa** en el servidor de producción: el árbol de trabajo
   está limpio, se conocen el commit y la migración en ejecución, y los
   servicios están activos.

   ```bash
   git status --short
   git rev-parse --short HEAD
   venv/bin/flask --app run.py db current
   systemctl is-active iso9001 iso9001-celery-worker iso9001-celery-beat iso9001-mcp
   ```

2. **Copias de seguridad** antes de cualquier cambio, cada fichero con modo
   `600` y con el nombre del commit que se despliega:
   - la base de datos, en el formato personalizado de PostgreSQL, y una
     comprobación de que el volcado se puede leer:

     ```bash
     pg_dump -Fc -f <backups>/<database>-<date>-pre-<new>.dump <database-url>
     pg_restore -l <backups>/<database>-<date>-pre-<new>.dump | wc -l
     ```

     Para una base de datos en un contenedor, ejecute `pg_dump` dentro de él
     (`docker exec <container> pg_dump -Fc -U <user> <database> > <file>`);
   - un archivo tar de la copia de trabajo,
     `iso9001-code-<date>-pre-<new>.tar.gz` (el directorio de documentos queda
     cubierto por la copia de seguridad periódica; véase
     [Copias de seguridad y restauración](#copias-de-seguridad-y-restauración));
   - `.env`, cuando la versión cambia la configuración.
3. **Código**: en un puesto de trabajo con el repositorio, empaquete los
   commits nuevos en un *bundle* de git y cópielo al servidor; allí,
   verifíquelo y avance la rama sin fusión (*fast-forward*):

   ```bash
   # workstation
   git bundle create iso9001-<old>..<new>.bundle <old>..main
   # production host
   git bundle verify <path>/iso9001-<old>..<new>.bundle
   git fetch <path>/iso9001-<old>..<new>.bundle main
   git merge --ff-only FETCH_HEAD
   ```

4. **Dependencias**, cuando `requirements.txt` ha cambiado:

   ```bash
   venv/bin/pip install -r requirements.txt
   venv/bin/pip check
   ```

5. **Base de datos**, antes del reinicio:

   ```bash
   venv/bin/flask --app run.py db upgrade
   venv/bin/flask --app run.py db check
   ```

6. **Configuración**: añada a `.env` cualquier variable nueva que necesite la
   versión.
7. **Reinicio** (operador con `sudo`) de los cuatro servicios, justo después
   de la migración:

   ```bash
   sudo systemctl restart iso9001 iso9001-celery-worker iso9001-celery-beat iso9001-mcp
   ```

8. **Pruebas de humo**:
   - los cuatro servicios están activos y el journal no tiene entradas de error
     desde el reinicio (`journalctl -u <unit> -p err --since "<restart time>"`);
   - `/login` responde `200`; `/` y las páginas que modifica la versión
     redirigen al inicio de sesión cuando no hay una sesión iniciada;
   - `/mcp` responde `401` con `WWW-Authenticate: Bearer` cuando no se envía
     token;
   - el servidor MCP enumera los módulos esperados (por ejemplo, con un cliente
     MCP; véase [`mcp.md`](mcp.md#smoke-test));
   - en una versión que cambia el calendario, el registro de beat muestra la
     nueva entrada.

Para volver atrás, restaure el código y la base de datos a partir de las
copias de seguridad del paso 2 (véase [Restauración](#restauración)).

## Copias de seguridad y restauración

Una copia de seguridad completa consta de tres elementos tomados en el mismo
momento:

1. **La base de datos**: `pg_dump -Fc`, comprobado con `pg_restore -l` (véase
   el [procedimiento de despliegue](#procedimiento-de-despliegue)).
2. **El directorio de adjuntos**, `DOCUMENT_STORAGE_DIR` (por defecto
   `instance/documents` en la copia de trabajo). La base de datos solo
   registra qué fichero corresponde a cada revisión, así que una copia de la
   base de datos sin este directorio pierde todos los documentos adjuntos.
   Debe formar parte de las copias de seguridad periódicas del servidor.
3. **`.env`**, que contiene `SECRET_KEY` (necesaria para que las sesiones, los
   enlaces de restablecimiento y todos los tokens de API sigan siendo válidos)
   y las credenciales. Guárdelo cifrado o con modo `600`.

El propio código está en git; el archivo tar tomado antes de un despliegue
cubre el estado local, como `logs/` y `celerybeat-schedule`.

### Restauración

1. Detenga los cuatro servicios.
2. Restaure la base de datos a partir del volcado, preferiblemente en una base
   de datos nueva y vacía a la que apunte después `DATABASE_URI`:

   ```bash
   pg_restore --no-owner -d <database-url> <file>.dump
   ```

3. Restaure `DOCUMENT_STORAGE_DIR` del mismo momento, con el usuario del
   servicio como propietario y modo `700`.
4. Haga que el código coincida con la base de datos: `flask db current` debe
   imprimir la revisión que imprime `flask db heads` para el código extraído.
   Ejecute también `flask db check`.
5. Arranque los servicios y ejecute las pruebas de humo del procedimiento de
   despliegue.
6. Obtenga un informe de los ficheros adjuntos sin referencia con
   `flask --app run.py cleanup-document-files --dry-run` y elimínelos sin
   `--dry-run` si el informe es el esperado.

## Monitorización y registros

| Fuente | Dónde | Qué |
|---|---|---|
| Estado de los servicios | `systemctl is-active <unit>` | Las cuatro unidades deben estar en `active` |
| Journal | `journalctl -u <unit>` | Mensajes de arranque de Gunicorn, el *worker* y el *beat* de Celery (`--loglevel=info`) y el servidor MCP |
| Registro de acceso de Gunicorn | `logs/gunicorn-access.log` | Dirección del extremo remoto, hora, estado, tamaño, User-Agent y duración de cada petición |
| Registro de errores de Gunicorn | `logs/gunicorn-error.log` | Errores de Gunicorn y de la aplicación |
| Registro de seguridad | `SECURITY_LOG_FILE` (`logs/security.log`) | Inicios de sesión y fallos, cierres de sesión, rechazos por límite de peticiones (`RATE_LIMIT_EXCEEDED`), solicitudes y cambios de restablecimiento de contraseña, estado de las cuentas, cambios de correo electrónico y de enlaces de restablecimiento, tokens de API emitidos, revocados y fallidos (`API_TOKEN_AUTH_FAILED`, `API_TOKEN_RATE_LIMITED`, `MCP_AUTH_RATE_LIMITED`), subidas y rechazos de adjuntos |
| Registro de auditoría | Base de datos | Cada cambio hecho a través de los servicios, con el actor y el canal |

- **Privacidad del registro de acceso**: `gunicorn.conf.py` omite
  deliberadamente el destino de la petición, la ruta, la cadena de consulta y
  el Referer, de modo que las URL de restablecimiento de contraseña no se
  registran. El User-Agent es un texto arbitrario que aporta quien hace la
  petición, y el extremo remoto puede ser el proxy; este formato no garantiza
  que se eliminen los datos sensibles de los registros de la aplicación ni de
  los del proxy.
- **Rotación**: nada en el repositorio rota estos ficheros. Rótelos con el
  logrotate del servidor; los procesos mantienen abierto el registro de
  seguridad, así que use `copytruncate` para él.
- **Sondas**: `/login` devuelve `200`; `/mcp` devuelve `401` sin token.
  `venv/bin/celery -A celery_worker.celery inspect ping` comprueba que el
  *worker* responde a través del intermediario; el registro de beat muestra
  cada tarea que envía.
- **Contenido**: el panel de control (`/dashboard/`) es la vista funcional:
  sus tarjetas enumeran las revisiones de documentos vencidas, los objetivos
  fuera de meta o con una medición vencida y las acciones de revisión por la
  dirección vencidas (véase la
  [guía de usuario](guia-de-usuario.md#panel-de-control)). Mantenga en marcha
  el *worker* y el *beat* de Celery, o los correos programados dejarán de
  enviarse sin ningún aviso.

## Notas de seguridad

- **Secretos**: `.env` contiene `SECRET_KEY` y las credenciales de la base de
  datos, Redis y SMTP. Git lo ignora; manténgalo con modo `600`. Rotar
  `SECRET_KEY` cierra la sesión de todos los usuarios e invalida todos los
  tokens de API y los enlaces de restablecimiento.
- **Modo de depuración**: no active nunca `FLASK_DEBUG` en producción; quita
  el atributo `Secure` de las cookies y elimina HSTS.
- **Proxies de confianza**: `TRUSTED_PROXIES` es la única vía por la que la
  dirección o el esquema de un cliente se toman de las cabeceras de proxy; los
  extremos remotos que no figuran en la lista no pueden establecerlos. `*` se
  rechaza al arrancar, y Gunicorn solo confía en las cabeceras de proxy que
  llegan desde loopback. Detrás de HTTPS, Flask-WTF comprueba que el `Referer`
  de un POST coincide con el host: los navegadores lo envían, y los clientes
  programados también deben enviarlo. El servidor MCP tiene su propio
  `MCP_TRUSTED_PROXIES`.
- **Límites de peticiones** (Flask-Limiter, por dirección de cliente salvo que
  se indique otra cosa): inicio de sesión, 5 POST por minuto; solicitudes de
  restablecimiento de contraseña, 3 por hora; enlaces de restablecimiento
  enviados por un administrador, 10 por hora; cambios de credenciales en la
  página de perfil, 10 por hora y cuenta. No hay un límite general para las
  páginas ordinarias. Configure `RATELIMIT_STORAGE_URI` con Redis para que
  todos los *workers* de Gunicorn compartan los contadores y estos se
  conserven tras los reinicios; con el valor por defecto, `memory://`, cada
  proceso lleva su propia cuenta. Si Redis no está accesible, los límites
  siguen funcionando con contadores en memoria hasta que se recupera. Los
  límites de MCP están en [`mcp.md`](mcp.md#rate-limits).
- **Cabeceras de seguridad** en todas las respuestas: una
  Content-Security-Policy que solo admite el origen propio de la aplicación
  (`default-src 'self'`; scripts, estilos y tipografías desde `'self'`;
  imágenes desde `'self'` y `data:`; `object-src 'none'`;
  `frame-ancestors 'self'`), `X-Frame-Options: SAMEORIGIN`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  strict-origin-when-cross-origin`, una `Permissions-Policy` restrictiva y
  HSTS durante un año fuera del modo de depuración. Todos los recursos
  (tipografías, Chart.js) se sirven desde el propio servidor, y las plantillas
  no contienen scripts, estilos ni manejadores de eventos en línea
  (`tests/test_ui_cierre.py` lo exige), de modo que la política no necesita
  `'unsafe-inline'`.
- **Cuentas**: no existe el registro público; el primer administrador se crea
  con `create-admin`. Un usuario desactivado pierde sus sesiones y sus tokens
  de API.

### Enlaces de restablecimiento de contraseña

- Los correos de restablecimiento necesitan `PASSWORD_RESET_BASE_URL`: solo el
  origen HTTPS raíz canónico, sin credenciales, ruta, consulta ni fragmento.
  Los enlaces nunca se construyen a partir del `Host` de la petición. Un valor
  ausente o no válido desactiva los correos de restablecimiento, pero no
  impide el arranque, el inicio de sesión ni el uso de la línea de órdenes.
- Un enlace de restablecimiento caduca al cabo de una hora y está vinculado al
  estado de la contraseña almacenada mediante una huella con clave secreta (el
  hash de la contraseña no figura en la URL). Un cambio de contraseña invalida
  todos los enlaces emitidos antes.
- Consumir un enlace es una actualización condicional de la base de datos, de
  modo que un enlace obsoleto no puede sobrescribir una contraseña más
  reciente y un enlace usado no puede reutilizarse
  (`tests/test_reset_concurrency.py` lo comprueba en PostgreSQL).
- Los casos de enlace no válido, caducado, de usuario desconocido, de enlace
  obsoleto y de fallo de la base de datos reciben todos la misma respuesta de
  enlace no válido.

### Dependencias

`requirements.txt` fija la versión de cada paquete. Actualizar una dependencia
supone cambiar su versión fijada en una *pull request*, para que la
integración continua ejecute las pruebas con ella, y después desplegarla con
`pip install -r requirements.txt` y `pip check`.

## Integración continua

GitHub Actions (`.github/workflows/ci.yml`) se ejecuta en cada *push*, en las
*pull requests* dirigidas a `main` y cuando se lanza manualmente. Su único
trabajo, `Python 3.11 tests`, en Ubuntu 24.04 y con un tiempo límite de 15
minutos:

1. arranca un servicio PostgreSQL 17;
2. instala las bibliotecas del sistema de WeasyPrint y `requirements.txt` con
   Python 3.11;
3. ejecuta `python -m unittest discover -s tests -p 'test_*.py' -v` con
   `TEST_POSTGRES_URI` apuntando a ese servicio.

Las pruebas de PostgreSQL (`tests/test_migrations.py` y otras) ejecutan las
migraciones reales y fallan si el esquema migrado difiere de los modelos. La
integración continua falla si falta `TEST_POSTGRES_URI`; en local, esas
pruebas se omiten salvo que esté definida. Eliminan y vuelven a crear el
esquema `public`, así que nunca haga que apunte a una base de datos real:

```bash
docker run -d --rm --name iso9001-test-db -e POSTGRES_PASSWORD=<password> \
  -p 127.0.0.1:55432:5432 postgres:17-alpine
TEST_POSTGRES_URI=postgresql://postgres:<password>@127.0.0.1:55432/postgres \
  venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

El nombre de la comprobación, `Python 3.11 tests`, es estable. Solo bloquea
las fusiones si un responsable del mantenimiento la configura como obligatoria
en las reglas del repositorio o en la protección de ramas.
