**Sistema de Gestión de Calidad con Flask y Celery**

Este sistema de gestión de calidad ha sido desarrollado en Flask. Utiliza Celery y Redis para la gestión de tareas en segundo plano, lo que permite enviar notificaciones periódicas y ejecutar otras tareas sin bloquear el funcionamiento principal de la aplicación.

**Tabla de Contenidos**

- Requisitos Previos
- Instalación
- Configuración de la Base de Datos
- Primera Toma de Contacto en Desarrollo
- Iniciar la Aplicación
- Mantenimiento y Supervisión
- Seguridad
- Arquitectura
- Actualizaciones y Despliegue en Producción
-----
**Requisitos Previos**

Antes de comenzar con la instalación, asegúrate de tener los siguientes elementos instalados y configurados:

- **Python 3.x** - Lenguaje de programación para ejecutar el proyecto.
- **Redis** - Para manejar las tareas de Celery en segundo plano.
- **PostgreSQL** - Sistema de gestión de bases de datos para almacenar los datos de la aplicación.
- **Servidor de Correo** (Gmail, etc.) - Para el envío de notificaciones por correo electrónico.

\### Instalación de Redis

Redis es necesario para manejar las tareas en segundo plano con Celery. A continuación, te explicamos cómo instalar Redis en diferentes sistemas operativos.

\#### macOS Si usas Homebrew, puedes instalar Redis con los siguientes comandos:

brew install redis 

brew services start redis # Para iniciar Redis como un servicio

\### En sistemas Ubuntu o Debian, instala Redis ejecutando:

sudo apt update

sudo apt install redis-server


-----
**Instalación**

**1. Clonar el Repositorio**

Clona el repositorio en tu máquina local:

git clone https://github.com/constant1n0/iso9001

cd iso9001

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

PASSWORD\_RESET\_BASE\_URL=https://qms.example.com

CELERY\_BROKER\_URL=redis://localhost:6379/0

CELERY\_RESULT\_BACKEND=redis://localhost:6379/0

FLASK\_APP=run.py

FLASK\_ENV=development  # Opcional, si quieres configurar el modo de desarrollo


**Nota:** Cambia usuario, contraseña, localhost, y db\_name con los valores correspondientes a tu configuración de PostgreSQL.

Para que las variables de entorno sean cargadas automáticamente, el proyecto utiliza **python-dotenv**, lo que permite que Flask lea las configuraciones directamente desde el archivo .env.

Password recovery email requires `PASSWORD_RESET_BASE_URL`. Configure it as
the canonical root HTTPS origin only, without credentials, a path, query, or
fragment. Missing or invalid configuration disables reset-email delivery but
does not prevent application startup, login, or local CLI use. Reset links
never fall back to the request Host.

Reset tokens expire after one hour and are bound to the current stored password
state through a secret-keyed fingerprint; the password hash is not placed in
the URL. A password change invalidates every token issued for the previous
state. Reset consumption uses a conditional database update, so a verified but
stale token cannot overwrite a newer password and a consumed token cannot be
replayed. Invalid, expired, unknown-user, stale, and database-failure cases use
the same invalid-link response. Tokens issued before this password-state
binding was introduced are rejected, so users must request a new link.

Rate limits apply only to the routes that need them: login (5 POSTs per
minute), password reset requests (3 per hour), administrator reset links (10
per hour) and credential changes (10 per hour per account); there is no
blanket limit on ordinary pages. In production set two more variables:

- `TRUSTED_PROXIES`: the reverse proxies whose `X-Forwarded-For` and
  `X-Forwarded-Proto` are honoured, as comma-separated addresses or CIDR
  networks, e.g. the Traefik Docker network `172.18.0.0/16`. Leave it empty
  when nothing sits in front of the application; `*` is refused at start-up.
  Gunicorn keeps its own default and trusts proxy headers only from loopback,
  so the application alone decides which peers may set them.
- `RATELIMIT_STORAGE_URI`: shared storage for the counters, e.g.
  `redis://localhost:6379/2` (a database index separate from Celery's), so
  every Gunicorn worker counts the same requests and counters survive
  restarts. It defaults to `memory://` (one counter per process). Because a
  storage is now always configured, Flask-Limiter no longer warns about
  in-memory storage at start-up. If Redis is unreachable, the limits keep
  working with in-memory counters until it recovers.

This protection requires no schema migration or additional token dependency
and does not revoke existing authenticated sessions. The isolated SQLite tests
exercise the compare-and-swap contract but are not proof of PostgreSQL
concurrency behavior in production. Rolling back the token model and reset
route restores replayable reset behavior; stop issuing links and allow the
one-hour validity window to expire before rollback unless that risk is accepted.

-----
**Configuración de la Base de Datos**

**1. Crear la Base de Datos en PostgreSQL**

Asegúrate de que el servicio PostgreSQL esté en ejecución y luego crea una base de datos para la aplicación.

psql -U usuario -d postgres

CREATE DATABASE db\_name;

\q

**2. Generar las Migraciones de la Base de Datos**

Asegúrate de que el entorno virtual esté activado y de que las configuraciones de DATABASE\_URI en .env sean correctas.

Ejecuta el comando para generar la carpeta migrations/

flask db init

Ejecuta el comando para generar migraciones de tus modelos en Flask:

flask db migrate -m "Creación de tablas iniciales"

Esto generará los archivos de migración en la carpeta migrations/, basados en los modelos definidos en el código.

` `**3. Aplicar Migraciones de Base de Datos**

Una vez generadas las migraciones, aplica los cambios en la base de datos:

flask db upgrade

Este comando creará todas las tablas en la base de datos especificada, utilizando las definiciones de los modelos en el proyecto.

**Nota**: Cada vez que realices cambios en los modelos, debes ejecutar flask db migrate y luego flask db upgrade para actualizar la estructura de la base de datos.

**Primera Toma de Contacto en Desarrollo**

**1. Arranque de la Aplicación en el Entorno de Desarrollo**

- Asegúrate de que el entorno virtual está activado (source venv/bin/activate).
- Ejecuta el servidor de Flask:

flask run

- Verifica que la aplicación esté funcionando en http://127.0.0.1:5000.

**2. Create the Initial Administrator Locally**

Public user registration is disabled. Create the initial administrator from a
trusted local shell and enter the password only at the hidden prompts:

```bash
venv/bin/flask --app run.py create-admin
```

The command refuses to run after any account exists and never modifies existing
accounts. Run it once: it performs a second existence check before committing,
but does not provide cross-process serialization for simultaneous local command
invocations.

**2b. API Tokens for Agents**

An administrator issues revocable API tokens from the same trusted local shell.
A token lets an agent adapter (the MCP server, see [`docs/mcp.md`](docs/mcp.md)) act as one user, with
that user's role narrowed by the token's scopes (`read`, `write`):

```bash
venv/bin/flask --app run.py create-api-token --user ana --name "Claude Code"            # read only, 90 days
venv/bin/flask --app run.py create-api-token --user ana --name "Bot" --scope read --scope write --days 30
venv/bin/flask --app run.py list-api-tokens [--user ana]
venv/bin/flask --app run.py revoke-api-token a1b2c3d4
```

`create-api-token` prints the token (`iso_<prefix>_<secret>`) once; only a keyed
hash is stored, so a lost token cannot be recovered and must be replaced.
Expiry is 90 days by default and at most 365. Changing `SECRET_KEY` invalidates
every token. `list-api-tokens` shows the prefix, owner, scopes and status, never
a secret. Issuing and revoking are written to the audit log and the security log.

AI agents (Claude Code, Codex, Pi, OpenCode, OpenClaw, Claude Desktop) reach the
QMS through the MCP server in `app/mcp_server/`; `docs/mcp.md` explains how to run
it, route it and configure each client.

**2c. Usuarios y perfil**

Once the first administrator exists, accounts are managed from the web:

- **Administración › Usuarios** (`/usuarios/`): administrators create users
  (username, e-mail, role and an initial password of at least 8 characters),
  change their e-mail and role, deactivate and reactivate them, and e-mail them
  a password-reset link. Auditors see the list read-only; operators cannot open
  it. Users are deactivated, never deleted: a deactivated user cannot log in,
  loses open sessions and has their active API tokens revoked, while the audit
  trail keeps naming them. Nobody can change their own role or deactivate
  themselves, and the last active administrator cannot be demoted or
  deactivated.
- **Mi perfil** (`/perfil/`, the user name in the top bar): every user sees
  their username, e-mail, role and account state, changes their e-mail or
  password (the current password is required), and lists and revokes their own
  API tokens. Token secrets and hashes are never shown, and tokens are still
  issued only from the command line.

Account changes are written to the audit log and the security log.

**2d. Personas y competencia**

The **Personas y competencia** navigation group records who does the work
under the QMS and whether they are competent for it (ISO 9001 clauses 5.3 and
7.2):

- **Personas** (`/personas/`): people who work under the QMS, separate from
  login accounts. A person has a name, an optional e-mail, notes, an active
  flag, any number of QMS roles (*Roles y responsabilidades*) and at most one
  linked user account. The list filters by name, state and role. A person
  that other records cite cannot be deleted: deactivate them instead. A
  person's page lists the competence they have demonstrated.
- **Competencias requeridas** (`/competencias/requisitos/`): the competence
  each role requires (education, training, skill or experience), with a
  description and how it is evidenced. A role that a requirement cites cannot
  be deleted.
- **Demonstrated competence** (from a person's page): the evidence (text or a
  reference), an optional training as evidence, the date obtained, an optional
  expiry date and the effectiveness evaluation (*Pendiente*, *Eficaz* or *No
  eficaz*; anything but pending needs the evaluation date and the evaluating
  person).
- **Matriz de competencias** (`/competencias/matriz`, optional role filter):
  for every role with requirements, its active people against each
  requirement, judged on today's date in the application's time zone. A
  record without an expiry date never expires, and one expiring today is
  still valid. Each cell links to the person's page:
  - *Cumplida*: an unexpired record evaluated effective (even if a newer
    attempt was not);
  - *Pendiente de evaluación* or *No eficaz*: otherwise, the newest unexpired
    record is pending or not effective;
  - *Caducada*: every record has expired;
  - *Falta*: there is no record.
- **Person pickers**: the training, nonconformity and audit forms pick a
  person. The free-text name (*personal*, *responsable*, *auditor*) stays as
  the legacy value: existing records keep their text, which is never matched
  to a person, and a name left blank takes the picked person's name. The text
  is required only when no person is picked. Only active people are offered,
  and a record keeps a person deactivated later.

Access is the same on the web and through the MCP server: every role reads
people, requirements, demonstrated competence and the matrix; administrators
and auditors create and edit; only administrators delete; the MCP server never
deletes. Every change is written to the audit log.

**2e. No conformidades y acciones correctivas**

Nonconformities (`/no_conformidades/`, ISO 9001 clause 10.2) record what went
wrong, how it was contained and corrected, and whether the correction worked:

- **Fields**: description, detection date, origin (*Auditoría*, *Cliente*,
  *Proceso*, *Proveedor*, *Otro*), severity (*Mayor*, *Menor*, *Observación*),
  the responsible person (plus the legacy free-text name), containment and
  root cause. The old free-text corrective action is kept read-only as
  *Acción correctiva (texto anterior)*.
- **States**: *Abierta* → *Acción planificada* → *En verificación* →
  *Cerrada*, plus *Cancelada*. Nobody edits the state: while the
  nonconformity is open it follows its corrective actions, and closing,
  cancelling and reopening are buttons on the nonconformity's page.
  - The first action moves an *Abierta* nonconformity to *Acción planificada*.
  - Once every action is done, it moves to *En verificación*.
  - When the latest action is verified not effective, it goes back to *Acción
    planificada*: register a new action.
- **Corrective actions** (on the nonconformity's page): description, owner (a
  person), planned date and done date (never before the detection date). The
  status is derived: *Planificada*, *Realizada*, *Verificada eficaz* or
  *Verificada no eficaz*. Every role adds and edits actions, only
  administrators delete them, and a verified action is read-only.
- **Verification of effectiveness**: an administrator or an auditor records
  the result (*Eficaz* or *No eficaz*), the date (not before the done date),
  the verifying person and the evidence. The verifier is never the action's
  owner; the picker leaves the owner out.
- **Closing**: administrators and auditors close a nonconformity once every
  action is done and verified and the latest one proved effective. An earlier
  ineffective action stays as evidence and does not block closing once a
  later action proved effective. While something is missing, the page lists
  it instead of the button. (Edge case: if an earlier action is found
  ineffective while a later one is already done, the nonconformity stays *En
  verificación*.)
- **Cancelling and reopening**: administrators and auditors cancel an open
  nonconformity with a reason. A closed or cancelled nonconformity, and its
  actions, are read-only until an administrator reopens it; it then returns
  to the state its actions call for, without the closing date or the reason.
- **List filters**: description, state, origin, severity and detection date.
  An unknown value in the URL is ignored.
- **PDF**: a nonconformity's PDF holds its fields, the closing or cancellation
  date and reason, and a table of its corrective actions with their status
  and verification (result, date, verifier and evidence).

The MCP server reads and writes nonconformities and corrective actions with
the same rules, but verifying, closing, cancelling and reopening are web-only
(see `docs/mcp.md`). Every change is written to the audit log.

**2f. Control documental**

Documents (`/documents/`, ISO 9001 clause 7.5) keep their identity (code,
title, category), an owner (a person) and a next review date; their text lives
in numbered revisions (1, 2, 3…) that are reviewed and approved before they
take effect.

- **States**: *Borrador* → *En revisión* → *Aprobado* → *Vigente* →
  *Obsoleto*. Rejecting a revision in review returns it to *Borrador* with the
  reason. A document has at most one revision in preparation (draft, in review
  or approved) and at most one in force. Editing a document in force starts a
  new draft from the text in force.
- **Roles**: every role reads the revision in force. Drafts, revisions in
  review and approved revisions not yet published are visible only to
  administrators and auditors, who create documents, write drafts, submit,
  reject and publish. Only administrators approve and withdraw.
- **Approver**: an administrator approves a revision in review, naming the
  approving person. The approver is never the revision's author, neither as
  the person named nor as the person linked to the approving user. The approve
  and reject pages only open for a revision in review.
- **Publication and obsolescence**: publishing an approved revision puts it in
  force from today (in `APP_TIMEZONE`); the revision it replaces becomes
  obsolete the same day and stays in the history as evidence. Revisions in
  force or obsolete never change again.
- **Withdrawal**: documents are never deleted. An administrator withdraws a
  document with a reason; its revision in force becomes obsolete, the date,
  reason and person are recorded, and the document becomes read-only.
- **Code**: the code is unique. It can be changed until a revision has been
  published; from then on the edit form shows it read-only and the change is
  refused.
- **List**: filters by category, owner, status (*Vigente*, *Sin publicar*,
  *De baja*) and overdue review; they run in the database.
- **Periodic review**: a document in use whose next review date has passed is
  flagged *Vencida* on the list. The dashboard card *Revisión de documentos*
  lists the reviews overdue or due within 30 days (an operativo sees only
  documents in force), and every Monday the e-mail alert below tells each
  owner and the administrators which reviews are due. Updating the next review
  date clears the flag.

The MCP server reads documents and their revision in force only (see
`docs/mcp.md`). Every change is written to the audit log.

*Attachments.* Each document revision may carry one file: PDF, DOCX, XLSX or ODT. The type is
checked by content as well as by extension, so a renamed executable or web page
is refused. Administrators and auditors attach, replace or remove the file of a
draft (*Borrador*) on the document's page. They can also discard a later draft
with its file. Revision 1 of a document that has never been in force cannot be
discarded; withdraw the document instead. Once a revision is in force, it and
its file never change.

- **Storage**: files live on the server's disk in `DOCUMENT_STORAGE_DIR`. It
  defaults to `instance/documents`, is created at start-up and is never under
  `static/`. Each file gets a random name; the original name, size, SHA-256 and
  type are kept in the database. Files are served only through the download
  route. Every role downloads the file of the revision in force; only
  administrators and auditors download the files of other revisions.
- **Size**: `DOCUMENT_MAX_BYTES` (default `20971520`, 20 MB) is the largest
  file accepted. Requests above it plus 1 MB for the form get a 413 response.
  If a reverse proxy sits in front, its own body limit must be at least as
  large (for Nginx, `client_max_body_size 21m;`).
- **Backups**: the database only records which file belongs to which revision.
  Back up `DOCUMENT_STORAGE_DIR` together with the database dump, and restore
  both together. Set the variable to a persistent path outside the code
  checkout in production.
- **Cleanup**: a replaced or removed file is deleted after the change is
  saved. If that delete fails, or the database is restored to an earlier
  point, unreferenced files can remain. Remove them with:

  ```bash
  venv/bin/flask --app run.py cleanup-document-files --dry-run   # report only
  venv/bin/flask --app run.py cleanup-document-files
  ```

  The command only reads the database. It keeps files changed in the last hour
  (an upload may still be saving) and anything that is not a stored file.

Uploads, removals and discarded drafts are written to the security log with the
user, document, revision, file name, size and SHA-256.

**3. Iniciar Redis y Celery para las Notificaciones Programadas**

Celery envía cuatro avisos por correo; la aplicación web funciona sin él.

| Tarea | Destinatarios | Cuándo |
|-------|---------------|--------|
| `iso9001.send_upcoming_audits_alert` | Usuarios con rol Auditor | Cada día a las 7:00 |
| `iso9001.send_pending_audits_report` | Usuarios con rol Administrador | Los lunes a las 8:00 |
| `iso9001.send_monthly_quality_report` | Usuarios con rol Administrador | El día 1 de cada mes a las 8:00 (PDF adjunto) |
| `iso9001.send_document_review_alert` | Propietarios de documentos y usuarios con rol Administrador | Los lunes a las 8:00 |

Las horas son locales a `APP_TIMEZONE` (por defecto `Europe/Madrid`). El aviso
diario incluye las auditorías pendientes o en proceso de los próximos 7 días;
el informe semanal, las pendientes; el informe mensual adjunta un PDF con
el total de auditorías, no conformidades y capacitaciones y la satisfacción media
registrados en el mes, las no conformidades del mes por estado y las acciones
correctivas verificadas en el mes (eficaces y no eficaces). El aviso de revisión
documental lista los documentos en uso cuya fecha de próxima revisión ya ha
llegado: cada propietario recibe los suyos a través del usuario vinculado a su
persona (un usuario Operativo, solo los vigentes) y los administradores reciben
la lista completa con el propietario de cada documento; los propietarios sin
usuario vinculado se omiten y quedan registrados en el log. Los usuarios
inactivos o sin correo se omiten. Si falla
el envío a algún destinatario, se sigue con el resto, se registra el error y la
tarea termina en fallo.

Requisitos: Redis accesible en `CELERY_BROKER_URL` y el correo configurado
(`MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER`). Con el mismo entorno
que la aplicación web, ejecuta desde la raíz del proyecto:

```bash
celery -A celery_worker.celery worker --loglevel=info
celery -A celery_worker.celery beat --loglevel=info
```

En producción, usa las unidades systemd `iso9001-celery-worker.service` e
`iso9001-celery-beat.service`. Para lanzar una tarea a mano con el worker en
marcha:

```bash
python -c "import celery_worker as w; w.send_upcoming_audits_alert.delay()"
```

**4. Acceso y Comprobación del Funcionamiento del Dashboard**

- Accede al dashboard de la aplicación en http://127.0.0.1:5000/dashboard tras iniciar sesión.
- Asegúrate de que los gráficos, estadísticas y notificaciones se muestran correctamente.

**5. Pruebas Básicas de Funcionalidad**

- Verifica formularios y rutas principales (ej. auditorías, capacitaciones).
- Prueba la funcionalidad de inicio y cierre de sesión para confirmar que los permisos y roles se manejan correctamente.

-----
**Mantenimiento y Supervisión**

**Tareas de Mantenimiento con Celery y Flask**

- **Celery**:
  - Asegúrate de que Celery esté en ejecución constantemente para que las tareas en segundo plano, como las notificaciones por correo y los reportes de auditoría, se ejecuten en el tiempo programado.
  - Las tareas programadas se definen en `celery_worker.py` (`celery.conf.beat_schedule`); ejecuta siempre worker y beat.
- **Dashboard**:
  - Revisa regularmente el dashboard de la aplicación para asegurarte de que muestra datos precisos. Los gráficos y notificaciones deben estar actualizados con la información de la base de datos.
  - En el dashboard podrás ver:
    - Estadísticas generales de auditorías, no conformidades, satisfacción del cliente y capacitaciones.
    - Alertas de auditorías próximas, no conformidades abiertas y capacitaciones cercanas.

**Monitoreo de Logs**

- **Logs de Flask**: Revisa los logs del servidor Flask para detectar cualquier error de la aplicación.
- **Logs de Celery**: Asegúrate de que no haya errores en el worker de Celery, especialmente para verificar que las tareas en segundo plano se ejecuten correctamente.
- **Gunicorn access logs**: `gunicorn.conf.py` deliberately omits the request target,
  path, query string, and Referer from access records so password-reset URLs are
  not recorded automatically. The retained fields are the connection peer, log
  time, response status and size, User-Agent, and request duration. User-Agent is
  arbitrary caller-supplied text, and the connection peer may be a reverse proxy;
  this format is not a general redaction guarantee for application or proxy logs.
-----
**Seguridad**

- **Protección de Claves**: Asegúrate de que el archivo .env nunca se suba al repositorio, ya que contiene credenciales sensibles. Está configurado en .gitignore para evitar que se suba accidentalmente.
- **Acceso al Dashboard**: Utiliza login\_required y la política central de permisos (require\_permission) para restringir el acceso a ciertas rutas y asegurar que solo usuarios autorizados puedan ver datos sensibles.
- **Actualizaciones de Dependencias**: Ejecuta actualizaciones regulares de las dependencias y verifica si hay parches de seguridad disponibles para Flask, Celery, y demás dependencias.
-----
**Arquitectura**

Las escrituras pasan por una capa de servicios independiente de Flask (permisos, validación, atribución y registro de auditoría). Consulta [docs/architecture/services.md](docs/architecture/services.md) para el funcionamiento, la matriz de permisos y cómo crear un adaptador nuevo, como el servidor MCP.

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

**Nota:** No olvides reiniciar Nginx tras modificar la configuración



iso9001/
├── sql
│   └── deployment.sql
├── run.py
├── requirements.txt
├── migrations
├── flaskapp.wsgi
├── celery_worker.py
├── iso9001-celery-worker.service
├── iso9001-celery-beat.service
├── app
│   ├── utils
│   │   ├── reports.py
│   │   ├── error_handlers.py
│   │   ├── permissions.py
│   │   └── __init__.py
│   ├── templates
│   │   ├── satisfaccion_cliente
│   │   │   ├── pdf_template.html
│   │   │   ├── nueva.html
│   │   │   ├── listar.html
│   │   │   └── editar.html
│   │   ├── reportes
│   │   │   └── reporte_mensual.html
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

## User interface

The UI uses a single stylesheet, `app/static/css/app.css`, with design tokens
(black, bone white and canary yellow `#FFF000`) defined as CSS custom
properties. All assets are served locally, so the Content-Security-Policy
allows no external origins:

- Fonts: Barlow Condensed, IBM Plex Sans and IBM Plex Mono (Fontsource 5.3.0,
  SIL Open Font License; see `app/static/fonts/*-OFL.txt`).
- Chart.js 4.5.1 (MIT; see `app/static/lib/chart.js-LICENSE.md`).

Shared template pieces live in `app/templates/_partials/`. Forms marked with
`data-confirm="…"` ask for confirmation through `app/static/js/app.js`; do not
use inline `onclick`/`onsubmit` handlers.

## Continuous Integration

GitHub Actions runs the Python 3.11 test suite on every push, on pull requests
targeting `main`, and when started manually. Run the same suite locally with:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

`tests/test_migrations.py` runs the real Alembic migrations and fails if the
resulting schema differs from the models. The migrations use PostgreSQL-only
DDL, so these tests need a disposable PostgreSQL database; CI provides one and
fails if it is missing. Locally they are skipped unless you set
`TEST_POSTGRES_URI`. The tests drop and recreate the `public` schema, so never
point it at a real database:

```bash
docker run -d --rm --name iso9001-test-db -e POSTGRES_PASSWORD=test \
  -p 127.0.0.1:55432:5432 postgres:17-alpine
TEST_POSTGRES_URI=postgresql://postgres:test@127.0.0.1:55432/postgres \
  venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

The stable check name is `Python 3.11 tests`. The workflow makes this check
available, but it does not block merges unless a maintainer separately configures
that check as required in the repository rules or branch protection settings.

## Licencia

Este proyecto está licenciado bajo la Licencia Pública General GNU v3.0. Para más detalles, consulta el archivo [LICENSE](./LICENSE).
