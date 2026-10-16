# Operations guide

How to install, configure, run, deploy, back up and monitor the ISO 9001 QMS.
Every command and variable here is taken from the code at the time of writing:
`app/config.py`, `app/__init__.py`, `app/commands.py`, `celery_worker.py`,
`gunicorn.conf.py`, the systemd units in the repository root and
`.github/workflows/ci.yml`. The MCP server has its own guide,
[`mcp.md`](mcp.md); the service layer is described in
[`architecture/services.md`](architecture/services.md).

Commands run from the root of the checkout with the virtual environment in
`venv/`. Placeholders are written as `<like-this>`; never put real passwords,
tokens or host names in files under version control.

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [PostgreSQL and migrations](#postgresql-and-migrations)
- [Redis and Celery](#redis-and-celery)
- [Users and the first administrator](#users-and-the-first-administrator)
- [API tokens](#api-tokens)
- [Document attachments](#document-attachments)
- [Running in development](#running-in-development)
- [Running in production](#running-in-production)
- [Deployment procedure](#deployment-procedure)
- [Backups and restore](#backups-and-restore)
- [Monitoring and logs](#monitoring-and-logs)
- [Security notes](#security-notes)
- [Continuous integration](#continuous-integration)

## Requirements

- **Python 3.11**, the version CI tests (`requirements.txt` is a pinned
  freeze).
- **PostgreSQL**. CI and production use PostgreSQL 17. The migrations use
  PostgreSQL-only DDL, so no other database is supported.
- **Redis**, for the Celery broker and, in production, the rate-limit
  counters. The web application itself runs without it.
- **An SMTP account** for e-mail: password-reset links and the scheduled
  notifications. The server is fixed in code (see
  [Settings fixed in code](#settings-fixed-in-code)).
- **WeasyPrint's system libraries** for the PDFs. On Ubuntu, CI installs:

  ```bash
  sudo apt-get install --no-install-recommends \
    libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libharfbuzz-subset0
  ```

Redis can be installed from the distribution (`sudo apt install redis-server`
on Ubuntu or Debian; `brew install redis` and `brew services start redis` on
macOS) or run as a container.

## Installation

```bash
git clone https://github.com/constant1n0/iso9001.git
cd iso9001
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/pip check
```

Then write `.env` in the checkout root (see [Configuration](#configuration)),
create the database and apply the migrations (see
[PostgreSQL and migrations](#postgresql-and-migrations)), and create the first
administrator (see [Users and the first administrator](#users-and-the-first-administrator)).

## Configuration

The application reads its settings from environment variables. `app/config.py`
calls `python-dotenv`, so a `.env` file in the checkout root is loaded by the
web application, the CLI commands, Celery and the MCP server alike; the
systemd units also pass it as `EnvironmentFile`. `.env` is listed in
`.gitignore`: keep it out of version control and readable only by the service
user (mode `600`). The tracked `.env.example` is a starting point; check it
against the tables below, which come from the code.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | none (required) | Signs sessions, CSRF tokens and password-reset links, and keys the API-token hashes. The application refuses to start without it. Changing it signs everybody out and invalidates every API token |
| `DATABASE_URI` | none (required) | SQLAlchemy URL of the PostgreSQL database, for example `postgresql://<user>:<password>@<host>:5432/<database>`. The application refuses to start without it |
| `FLASK_DEBUG` | `False` | `true`, `1` or `yes` turns on debug mode. In debug mode the session and remember-me cookies are not marked `Secure` and HSTS is not sent. Never set it in production |
| `APP_TIMEZONE` | `Europe/Madrid` | Time zone for "today" (approval, publication and closing dates, overdue checks, report periods) and for the Celery schedule |
| `MAIL_USERNAME` | none | SMTP login |
| `MAIL_PASSWORD` | none | SMTP password |
| `MAIL_DEFAULT_SENDER` | none | Sender address of every e-mail |
| `PASSWORD_RESET_BASE_URL` | none | Root HTTPS origin used to build password-reset links, for example `https://qms.example.com`. See [Password reset links](#password-reset-links) |
| `TRUSTED_PROXIES` | empty | Reverse proxies whose `X-Forwarded-For` and `X-Forwarded-Proto` are honoured: comma-separated addresses or CIDR networks. Empty means none. `*` or an invalid entry stops start-up. See [Reverse proxy](#reverse-proxy) |
| `RATELIMIT_STORAGE_URI` | `memory://` | Storage for the rate-limit counters, shared by the web application and the MCP server. Use Redis in production, for example `redis://:<password>@127.0.0.1:6379/2`, in a database index separate from Celery's |
| `MCP_TOKEN_RATE_LIMIT` | `120/minute` | MCP HTTP transport: requests per API token. See [`mcp.md`](mcp.md#rate-limits) |
| `MCP_AUTH_FAILURE_RATE_LIMIT` | `20/minute` | MCP HTTP transport: failed bearer authentications per client address |
| `DOCUMENT_STORAGE_DIR` | `instance/documents` | Directory of the document-revision attachments (`<instance path>/documents`; the instance path is `instance/` in the checkout). Created at start-up with mode `700`; start-up fails if it lies under `app/static/`. See [Document attachments](#document-attachments) |
| `DOCUMENT_MAX_BYTES` | `20971520` (20 MiB) | Largest attachment accepted, in bytes. Must be a positive integer or start-up fails. Requests above this plus 1 MiB for the form get `413` |
| `SECURITY_LOG_ENABLED` | `True` | `true`, `1` or `yes` writes the security log; any other value turns it off |
| `SECURITY_LOG_FILE` | `logs/security.log` | Path of the security log (relative default: `logs/` in the checkout root, created when needed) |
| `CELERY_BROKER_URL` | none | Celery broker, for example `redis://:<password>@127.0.0.1:6379/0`. Required by the Celery worker and beat, which refuse to start without it; the web application does not use it |
| `CELERY_RESULT_BACKEND` | none | Optional. Tasks ignore their results, so it can stay unset |
| `GUNICORN_BIND` | `0.0.0.0:5000` | Read by `gunicorn.conf.py`: address and port Gunicorn listens on |
| `GUNICORN_WORKERS` | `2 × CPUs + 1` | Read by `gunicorn.conf.py`: number of worker processes |
| `GUNICORN_THREADS` | `2` | Read by `gunicorn.conf.py`: threads per worker |

The MCP server (`python -m app.mcp_server`) also reads `MCP_HOST` (default
`127.0.0.1`), `MCP_PORT` (`8765`), `MCP_ALLOWED_HOSTS`, `MCP_TRUSTED_PROXIES`
(`127.0.0.1`) and, for the stdio transport only, `ISO9001_MCP_TOKEN`; their
meaning is in [`mcp.md`](mcp.md#server-configuration).

The tests read `TEST_POSTGRES_URI` (see [Continuous integration](#continuous-integration)).

`FLASK_APP` is not needed: the commands in this guide pass `--app run.py`.
`FLASK_ENV` is no longer read by Flask 3; use `FLASK_DEBUG`.

### Settings fixed in code

These are set in `app/config.py`, not from the environment:

- Mail: `MAIL_SERVER = smtp.gmail.com`, `MAIL_PORT = 587`, `MAIL_USE_TLS = True`.
  Another SMTP provider needs a code change.
- The session cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` unless
  `FLASK_DEBUG` is on. A permanent-session lifetime (8 hours) and remember-me
  settings (7 days, same flags) are configured, but sign-in creates neither a
  permanent session nor a remember-me cookie, so a session ends when the
  browser closes.
- Rate-limit keys use the prefix `iso9001`; a Redis storage gets 1-second
  connect and socket timeouts, and in-memory counters stand in while it is
  unreachable.

## PostgreSQL and migrations

Create a role and a database for the application, then apply the migrations
that ship in `migrations/` (Flask-Migrate over Alembic):

```bash
psql -U postgres -c "CREATE ROLE <user> LOGIN PASSWORD '<password>';"
psql -U postgres -c "CREATE DATABASE <database> OWNER <user>;"
venv/bin/flask --app run.py db upgrade
venv/bin/flask --app run.py db check     # the models match the migrated schema
venv/bin/flask --app run.py db current   # the revision the database is at
```

`flask db upgrade` creates every table from an empty database and brings an
existing one up to the newest revision. Never run `flask db init`: the
`migrations/` directory already exists. A change to the models ships with its
own migration, generated with `flask db migrate -m "<message>"` against an
up-to-date database and reviewed by hand; `tests/test_migrations.py` fails if
the migrated schema differs from the models.

`sql/deployment.sql` is a legacy schema script from before the migrations. It
is not used and does not match the models.

## Redis and Celery

Celery sends four scheduled e-mails. The web application works without it.
The schedule is `celery.conf.beat_schedule` in `celery_worker.py`; the times
are local to `APP_TIMEZONE`.

| Beat entry | Task | Recipients | When |
|---|---|---|---|
| `upcoming-audits-alert-daily` | `iso9001.send_upcoming_audits_alert` | Users with the Auditor role | Every day at 07:00 |
| `pending-audits-report-weekly` | `iso9001.send_pending_audits_report` | Users with the Administrator role | Mondays at 08:00 |
| `monthly-quality-report` | `iso9001.send_monthly_quality_report` | Users with the Administrator role | The 1st of every month at 08:00, with a PDF of the month that ended |
| `document-review-alert-weekly` | `iso9001.send_document_review_alert` | Document owners (through the user linked to their person) and administrators | Mondays at 08:00 |

- The daily alert lists the pending or in-progress audits of the next 7 days;
  the weekly report lists the pending audits; the document review alert lists
  the documents in use whose next review date has arrived. What each message
  contains is described in the [user guide](user-guide.md).
- One message goes to each recipient. Inactive users and users without an
  e-mail address are skipped (and logged); so are document owners without a
  linked user. If a delivery fails, the task logs it, carries on with the
  other recipients and ends in failure.

Run the worker and exactly one beat with the same environment as the web
application:

```bash
venv/bin/celery -A celery_worker.celery worker --loglevel=info
venv/bin/celery -A celery_worker.celery beat --loglevel=info
```

Both need `CELERY_BROKER_URL` and the mail settings. Beat keeps its state in a
`celerybeat-schedule` file (ignored by git); the production unit passes
`--schedule=<checkout>/celerybeat-schedule`. To send a notification by hand
while the worker runs:

```bash
venv/bin/python -c "import celery_worker as w; w.send_upcoming_audits_alert.delay()"
```

## Users and the first administrator

Public registration does not exist. Create the first administrator from a
trusted shell on the server; the password is typed only at hidden prompts:

```bash
venv/bin/flask --app run.py create-admin
```

- It asks for a username (4 to 150 characters), an e-mail address and a
  password of at least 8 characters, twice.
- It refuses to run once any account exists and never modifies existing
  accounts. Run it once: it checks again before committing, but two
  simultaneous runs are not serialized.

Every other account is managed on the web by administrators
(*Administración › Usuarios*, `/usuarios/`): creating users, changing their
e-mail and role, deactivating and reactivating them, and sending password-reset
links. See the [user guide](user-guide.md).

## API tokens

API tokens let an agent act as one user through the MCP server, with that
user's role narrowed by the token's scopes (`read`, `write`). They are issued
only from a trusted shell on the server:

```bash
venv/bin/flask --app run.py create-api-token --user <username-or-email> --name "Claude Code"
venv/bin/flask --app run.py create-api-token --user <username-or-email> --name "Bot" --scope read --scope write --days 30
venv/bin/flask --app run.py list-api-tokens [--user <username-or-email>]
venv/bin/flask --app run.py revoke-api-token <prefix>
```

- `create-api-token` defaults to the `read` scope and 90 days; `--days` accepts
  1 to 365. It prints the token (`iso_<prefix>_<secret>`) once. Only a keyed
  hash is stored, so a lost token cannot be recovered and must be replaced.
- Changing `SECRET_KEY` invalidates every token.
- `list-api-tokens` shows the ID, prefix, owner, name, scopes, status, expiry
  and last use, never a secret.
- `revoke-api-token` takes the 8-character prefix.
- Issuing and revoking are written to the audit log and the security log. The
  commands act as an administrator through the `cli` channel, labelled with the
  operating-system account.

Users list and revoke their own tokens on their profile page (`/perfil/`);
deactivating a user revokes their active tokens. Client set-up, the HTTP
endpoint and its limits are in [`mcp.md`](mcp.md#issue-a-token); how an
adapter authenticates is in
[`architecture/services.md`](architecture/services.md#api-tokens).

## Document attachments

Each document revision may carry one file (PDF, DOCX, XLSX or ODT, checked by
content as well as by extension).

- **Storage**: files live in `DOCUMENT_STORAGE_DIR` (default
  `instance/documents`) under random names; the original name, size, SHA-256
  and type are kept in the database. They are served only through the
  authorized download route, never from `static/`.
- **Size**: `DOCUMENT_MAX_BYTES` (default 20 MiB). A reverse proxy in front
  must accept bodies at least that large plus 1 MiB (for Nginx,
  `client_max_body_size 21m;`).
- **Backups**: the database only records which file belongs to which revision.
  Back up the directory together with the database dump and restore both
  together (see [Backups and restore](#backups-and-restore)).
- **Location in production**: the systemd units run with
  `ProtectSystem=strict` and write access only to the checkout
  (`ReadWritePaths`). The default directory inside the checkout works as is; a
  directory elsewhere must be added to `ReadWritePaths` of the web unit, and
  the directory must persist across deployments.
- **Cleanup**: a replaced or removed file is deleted after the change is
  saved. If that delete fails, or the database is restored to an earlier
  point, unreferenced files can remain. Remove them with:

  ```bash
  venv/bin/flask --app run.py cleanup-document-files --dry-run   # report only
  venv/bin/flask --app run.py cleanup-document-files
  ```

  The command only reads the database. It keeps files changed in the last hour
  (an upload may still be saving) and anything that is not a stored file.

Uploads, removals, refused files and discarded drafts are written to the
security log with the user, document, revision, file name, size and SHA-256.

## Running in development

Use a disposable PostgreSQL, for example in a container:

```bash
docker run -d --name iso9001-dev-db -e POSTGRES_PASSWORD=<password> \
  -p 127.0.0.1:5432:5432 postgres:17-alpine
```

A minimal `.env` for development:

```bash
SECRET_KEY=<random-string>
DATABASE_URI=postgresql://postgres:<password>@127.0.0.1:5432/postgres
FLASK_DEBUG=1
```

Then apply the migrations, create the administrator and start the
development server:

```bash
venv/bin/flask --app run.py db upgrade
venv/bin/flask --app run.py create-admin
venv/bin/flask --app run.py run
```

Open `http://127.0.0.1:5000`, sign in, and check that the dashboard
(`/dashboard/`) shows its cards and charts. Without `CELERY_BROKER_URL` no
scheduled e-mail is sent; without the mail settings password-reset e-mails are
not sent either. Run the test suite with:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

## Running in production

Production runs four systemd services from one checkout and one `.env`:
Gunicorn for the web application, the Celery worker, Celery beat and the MCP
server. A reverse proxy terminates TLS in front of Gunicorn and the MCP server.

### Gunicorn

```bash
venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
```

`wsgi.py` creates the application; `gunicorn.conf.py` sets:

- the bind address, workers and threads from `GUNICORN_BIND`,
  `GUNICORN_WORKERS` and `GUNICORN_THREADS`. Bind to `127.0.0.1` (or to the
  address the proxy reaches) rather than the default `0.0.0.0`;
- a 120-second timeout, worker recycling after 1,000 requests (with jitter),
  and limits on the request line and headers;
- `preload_app = True`: a reload with `HUP` does not load new code, so restart
  the service after a deployment;
- the access log, error log and PID file at absolute paths under the
  production checkout's `logs/` directory and root; change them for another
  layout;
- an access-log format without the request path, query string or Referer (see
  [Monitoring and logs](#monitoring-and-logs)).

`gunicorn.conf.py` does not set `forwarded_allow_ips`, so Gunicorn trusts proxy
headers only from loopback (its default); the application's
`TRUSTED_PROXIES` decides the client address and scheme.

`flaskapp.wsgi` exposes the application as `application` for a mod_wsgi-style
server; the systemd units do not use it.

### Reverse proxy

The production host uses Traefik on the same machine: it terminates TLS and
routes the QMS host to Gunicorn and `/mcp` to the MCP server (see
[`mcp.md`](mcp.md#traefik-routing)). Any proxy must:

- terminate TLS: secure cookies, HSTS and the reset links assume HTTPS;
- keep the original `Host` header and send `X-Forwarded-For` and
  `X-Forwarded-Proto`, and be listed in `TRUSTED_PROXIES` (for Traefik in
  Docker, its network, for example `172.18.0.0/16`; for a proxy on the same
  host, `127.0.0.1`). Otherwise every user shares the proxy's address for the
  rate limits and the security log;
- accept request bodies of `DOCUMENT_MAX_BYTES` plus 1 MiB;
- not record request paths in its access log, because password-reset links
  carry their token in the path. On the production host the access log is
  turned off for the QMS route.

An equivalent Nginx site, with HTTPS from Let's Encrypt (for example through
Certbot):

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

With this site, set `TRUSTED_PROXIES=127.0.0.1` and reload Nginx after each
change.

### systemd units

The repository root holds the four units:

| Unit | Runs |
|---|---|
| `iso9001.service` | `gunicorn -c gunicorn.conf.py wsgi:app` (`Type=notify`; `reload` sends `HUP`) |
| `iso9001-celery-worker.service` | `celery -A celery_worker.celery worker --loglevel=info --concurrency=1` |
| `iso9001-celery-beat.service` | `celery -A celery_worker.celery beat --loglevel=info --schedule=<checkout>/celerybeat-schedule` |
| `iso9001-mcp.service` | `python -m app.mcp_server --transport http --host 127.0.0.1 --port 8765` |

They share the same user and group, `WorkingDirectory` (the checkout), the
virtual environment's `PATH`, `EnvironmentFile` (the checkout's `.env`),
`Restart=on-failure` and hardening: `PrivateTmp`, `NoNewPrivileges`,
`ProtectSystem=strict`, `ProtectHome=read-only` and write access only to the
checkout. They contain the production checkout path and account: edit `User`,
`Group`, `WorkingDirectory`, `PATH`, `EnvironmentFile`, `ExecStart` and
`ReadWritePaths` for another layout, then install them:

```bash
sudo cp iso9001*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iso9001 iso9001-celery-worker iso9001-celery-beat iso9001-mcp
```

If PostgreSQL or Redis run in containers, order the units after
`docker.service` (the Celery units already do). The MCP unit binds to
loopback; a proxy in a container reaches it through a systemd drop-in that
changes `--host`, plus a firewall rule limited to the proxy's network (see
[`mcp.md`](mcp.md#server-configuration)).

## Deployment procedure

The procedure used for every release on the production host. The production
host does not pull from GitHub; the agent or operator that deploys has no
`sudo`, and an operator with `sudo` restarts the services. Placeholders: `<old>`
is the commit running now, `<new>` the commit to deploy.

1. **Preflight** on the production host: the working tree is clean, the
   running commit and migration are known, and the services are active.

   ```bash
   git status --short
   git rev-parse --short HEAD
   venv/bin/flask --app run.py db current
   systemctl is-active iso9001 iso9001-celery-worker iso9001-celery-beat iso9001-mcp
   ```

2. **Backups** before any change, each file with mode `600`, named after the
   commit being deployed:
   - the database, in PostgreSQL's custom format, and a check that the dump
     can be read:

     ```bash
     pg_dump -Fc -f <backups>/<database>-<date>-pre-<new>.dump <database-url>
     pg_restore -l <backups>/<database>-<date>-pre-<new>.dump | wc -l
     ```

     For a database in a container, run `pg_dump` inside it
     (`docker exec <container> pg_dump -Fc -U <user> <database> > <file>`);
   - a tarball of the checkout, `iso9001-code-<date>-pre-<new>.tar.gz`
     (the documents directory is covered by the regular backup, see
     [Backups and restore](#backups-and-restore));
   - `.env`, when the release changes the configuration.
3. **Code**: on a workstation with the repository, bundle the new commits and
   copy the bundle to the host; there, verify it and fast-forward:

   ```bash
   # workstation
   git bundle create iso9001-<old>..<new>.bundle <old>..main
   # production host
   git bundle verify <path>/iso9001-<old>..<new>.bundle
   git fetch <path>/iso9001-<old>..<new>.bundle main
   git merge --ff-only FETCH_HEAD
   ```

4. **Dependencies**, when `requirements.txt` changed:

   ```bash
   venv/bin/pip install -r requirements.txt
   venv/bin/pip check
   ```

5. **Database**, before the restart:

   ```bash
   venv/bin/flask --app run.py db upgrade
   venv/bin/flask --app run.py db check
   ```

6. **Configuration**: add any new variable the release needs to `.env`.
7. **Restart** (operator with `sudo`) all four services, right after the
   migration:

   ```bash
   sudo systemctl restart iso9001 iso9001-celery-worker iso9001-celery-beat iso9001-mcp
   ```

8. **Smoke tests**:
   - all four services are active, and the journal has no error entries since
     the restart (`journalctl -u <unit> -p err --since "<restart time>"`);
   - `/login` answers `200`; `/` and the pages the release touched redirect to
     the login when signed out;
   - `/mcp` answers `401` with `WWW-Authenticate: Bearer` without a token;
   - the MCP server lists the expected modules (for example with an MCP client,
     see [`mcp.md`](mcp.md#smoke-test));
   - for a release that changes the schedule, the beat log lists the new entry.

To roll back, restore the code and the database from the backups of step 2
(see [Restore](#restore)).

## Backups and restore

A complete backup is three things taken at the same point in time:

1. **The database**: `pg_dump -Fc`, checked with `pg_restore -l` (see the
   [deployment procedure](#deployment-procedure)).
2. **The attachments directory**, `DOCUMENT_STORAGE_DIR` (by default
   `instance/documents` in the checkout). The database only records which file
   belongs to which revision, so a database backup without this directory
   loses every attached document. It must be part of the host's regular
   backups.
3. **`.env`**, which holds `SECRET_KEY` (needed to keep sessions, reset links
   and every API token valid) and the credentials. Store it encrypted or with
   mode `600`.

The code itself is in git; the tarball taken before a deployment covers local
state such as `logs/` and `celerybeat-schedule`.

### Restore

1. Stop the four services.
2. Restore the database from the dump, ideally into a new, empty database
   that `DATABASE_URI` then points to:

   ```bash
   pg_restore --no-owner -d <database-url> <file>.dump
   ```

3. Restore `DOCUMENT_STORAGE_DIR` from the same point in time, owned by the
   service user with mode `700`.
4. Make the code match the database: `flask db current` must print the
   revision that `flask db heads` prints for the checked-out code. Run
   `flask db check` as well.
5. Start the services and run the smoke tests of the deployment procedure.
6. Report unreferenced attachment files with
   `flask --app run.py cleanup-document-files --dry-run`, and remove them
   without `--dry-run` if the report is as expected.

## Monitoring and logs

| Source | Where | What |
|---|---|---|
| Service state | `systemctl is-active <unit>` | The four units should be `active` |
| Journal | `journalctl -u <unit>` | Gunicorn start-up messages, Celery worker and beat (`--loglevel=info`), the MCP server |
| Gunicorn access log | `logs/gunicorn-access.log` | Peer address, time, status, size, User-Agent and duration of each request |
| Gunicorn error log | `logs/gunicorn-error.log` | Gunicorn and application errors |
| Security log | `SECURITY_LOG_FILE` (`logs/security.log`) | Sign-ins and failures, sign-outs, rate-limit refusals (`RATE_LIMIT_EXCEEDED`), password-reset requests and changes, account status, e-mail and reset-link changes, API tokens issued, revoked and failing (`API_TOKEN_AUTH_FAILED`, `API_TOKEN_RATE_LIMITED`, `MCP_AUTH_RATE_LIMITED`), attachment uploads and refusals |
| Audit log | Database | Every change made through the services, with the actor and channel |

- **Access-log privacy**: `gunicorn.conf.py` deliberately omits the request
  target, path, query string and Referer, so password-reset URLs are not
  recorded. User-Agent is arbitrary caller-supplied text, and the peer may be
  the proxy; this format is not a redaction guarantee for application or proxy
  logs.
- **Rotation**: nothing in the repository rotates these files. Rotate them
  with the host's logrotate; the security log is held open by the processes,
  so use `copytruncate` for it.
- **Probes**: `/login` returns `200`; `/mcp` returns `401` without a token.
  `venv/bin/celery -A celery_worker.celery inspect ping` checks that the worker
  answers through the broker; the beat log shows each task it sends.
- **Content**: the dashboard (`/dashboard/`) is the functional view: its cards
  list overdue document reviews, objectives off target or with an overdue
  measurement, and overdue management-review actions (see the
  [user guide](user-guide.md)). Keep Celery worker and beat running, or the
  scheduled e-mails stop silently.

## Security notes

- **Secrets**: `.env` holds `SECRET_KEY`, the database, Redis and SMTP
  credentials. It is ignored by git; keep it at mode `600`. Rotating
  `SECRET_KEY` signs everybody out and invalidates every API token and reset
  link.
- **Debug mode**: never set `FLASK_DEBUG` in production; it removes the
  `Secure` flag from cookies and HSTS.
- **Trusted proxies**: `TRUSTED_PROXIES` is the only way a client address or
  scheme is taken from proxy headers; peers not listed cannot set them. `*`
  is refused at start-up, and Gunicorn trusts proxy headers only from
  loopback. Behind HTTPS, Flask-WTF checks that a POST's `Referer` matches the
  host: browsers send it, scripted clients must too. The MCP server has its own
  `MCP_TRUSTED_PROXIES`.
- **Rate limits** (Flask-Limiter, per client address unless noted): sign-in,
  5 POSTs per minute; password-reset requests, 3 per hour; administrator
  reset links, 10 per hour; credential changes on the profile page, 10 per
  hour per account. There is no blanket limit on ordinary pages. Set
  `RATELIMIT_STORAGE_URI` to Redis so every Gunicorn worker shares the
  counters and they survive restarts; with the default `memory://` each
  process counts on its own. If Redis is unreachable the limits keep working
  with in-memory counters until it recovers. The MCP limits are in
  [`mcp.md`](mcp.md#rate-limits).
- **Security headers** on every response: a Content-Security-Policy that
  allows only the application's own origin (`default-src 'self'`; scripts,
  styles and fonts from `'self'`; images from `'self'` and `data:`;
  `object-src 'none'`; `frame-ancestors 'self'`), `X-Frame-Options: SAMEORIGIN`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  strict-origin-when-cross-origin`, a restrictive `Permissions-Policy`, and
  HSTS for one year outside debug mode. Every asset (fonts, Chart.js) is
  served locally, and templates contain no inline scripts, styles or event
  handlers (`tests/test_ui_cierre.py` enforces it), so the policy needs no
  `'unsafe-inline'`.
- **Accounts**: public registration does not exist; the first administrator
  comes from `create-admin`. A deactivated user loses their sessions and API
  tokens.

### Password reset links

- Reset e-mails need `PASSWORD_RESET_BASE_URL`: the canonical root HTTPS
  origin only, without credentials, a path, query or fragment. Links are
  never built from the request's `Host`. A missing or invalid value disables
  reset e-mails but does not prevent start-up, sign-in or the CLI.
- A reset link expires after one hour and is bound to the stored password
  state through a secret-keyed fingerprint (the password hash is not in the
  URL). A password change invalidates every link issued before it.
- Consuming a link is a conditional database update, so a stale link cannot
  overwrite a newer password and a used link cannot be replayed
  (`tests/test_reset_concurrency.py` checks this on PostgreSQL).
- Invalid, expired, unknown-user, stale and database-failure cases all get the
  same invalid-link answer.

### Dependencies

`requirements.txt` pins every package. Updating a dependency means changing
its pin in a pull request, so CI runs the suite against it, and then deploying
it with `pip install -r requirements.txt` and `pip check`.

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs on every push, on pull
requests targeting `main`, and when started manually. Its single job,
`Python 3.11 tests`, on Ubuntu 24.04 with a 15-minute timeout:

1. starts a PostgreSQL 17 service;
2. installs WeasyPrint's system libraries and `requirements.txt` on Python
   3.11;
3. runs `python -m unittest discover -s tests -p 'test_*.py' -v` with
   `TEST_POSTGRES_URI` pointing at that service.

The PostgreSQL tests (`tests/test_migrations.py` and others) run the real
migrations and fail if the migrated schema differs from the models. CI fails
if `TEST_POSTGRES_URI` is missing; locally those tests are skipped unless it is
set. They drop and recreate the `public` schema, so never point it at a real
database:

```bash
docker run -d --rm --name iso9001-test-db -e POSTGRES_PASSWORD=<password> \
  -p 127.0.0.1:55432:5432 postgres:17-alpine
TEST_POSTGRES_URI=postgresql://postgres:<password>@127.0.0.1:55432/postgres \
  venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

The check name `Python 3.11 tests` is stable. It blocks merges only if a
maintainer configures it as required in the repository rules or branch
protection.
