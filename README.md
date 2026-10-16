# ISO 9001 QMS

A quality management system for ISO 9001:2026, built on Flask, PostgreSQL,
Celery and Redis. People work in a Spanish web interface; AI agents work on the
same records through an MCP server, under the same permissions, with revocable
API tokens. Every change goes through one service layer that checks
permissions, validates, attributes and writes the audit log.

## Modules

- **Dashboard**: key figures, charts and alerts (overdue document reviews,
  objectives off target, overdue management-review actions).
- **Users and profile**: accounts with the *Administrador*, *Auditor* and
  *Operativo* roles, deactivation instead of deletion, and each user's profile
  and API tokens.
- **People and competence** (clauses 5.3, 7.2): people, QMS roles, required and
  demonstrated competence, and the competence matrix.
- **Nonconformities and corrective actions** (10.2): containment, root cause,
  corrective actions, effectiveness verification, closing and reopening.
- **Document control** (7.5): numbered revisions reviewed, approved and
  published, attachments, withdrawal and periodic review.
- **Internal audits** (9.2): yearly audit programme, audits with scope, team
  and independence checks, and classified findings that open nonconformities.
- **Quality objectives** (6.2): indicator, target, owner, measurements, trend
  and overdue measurements.
- **Management review** (9.3): inputs drawn from the registers, conclusions,
  actions and the minutes PDF.
- **Earlier registers**: interested parties, risks and opportunities, roles and
  responsibilities, training and training resources, process operations, audit
  indicators, improvements and customer satisfaction.
- **Reports and notifications**: PDFs of the records, a monthly quality report,
  and scheduled e-mail alerts sent by Celery.
- **MCP server** (`app/mcp_server/`): Claude Code, Codex, Pi, OpenCode,
  OpenClaw and Claude Desktop read and write the QMS as a user.

## Quick start (development)

You need Python 3.11, a PostgreSQL database (Docker is the quickest way) and
WeasyPrint's system libraries for the PDFs (see
[requirements](docs/operations.md#requirements)).

```bash
git clone https://github.com/constant1n0/iso9001.git
cd iso9001
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt

docker run -d --name iso9001-dev-db -e POSTGRES_PASSWORD=<password> \
  -p 127.0.0.1:5432:5432 postgres:17-alpine

cat > .env <<'EOF'
SECRET_KEY=<random-string>
DATABASE_URI=postgresql://postgres:<password>@127.0.0.1:5432/postgres
FLASK_DEBUG=1
EOF

venv/bin/flask --app run.py db upgrade
venv/bin/flask --app run.py create-admin   # the first administrator, once
venv/bin/flask --app run.py run
```

Open `http://127.0.0.1:5000` and sign in. Celery, e-mail, production
settings and every environment variable are in the
[operations guide](docs/operations.md).

## AI agents

Administrators issue API tokens from a shell on the server with
`flask --app run.py create-api-token`, list them with `list-api-tokens` and
revoke them with `revoke-api-token` (see
[API tokens](docs/operations.md#api-tokens)). The MCP server, its deployment
and the configuration of each client are in [`docs/mcp.md`](docs/mcp.md).

## Documentation

| Document | For |
|---|---|
| [Operations guide](docs/operations.md) | Installation, configuration, PostgreSQL, Redis and Celery, Gunicorn, the reverse proxy and systemd, deployment, backups, monitoring, security, CI |
| [User guide](docs/user-guide.md) | Using every module, in English |
| [Guía de usuario](docs/guia-de-usuario.md) | The same guide, in Spanish |
| [MCP server](docs/mcp.md) | Tokens, the MCP server and AI-agent clients |
| [Service layer](docs/architecture/services.md) | Architecture, permission matrix, state rules, writing an adapter |
| [ISO 9001 gap analysis](docs/iso-9001-gap-analysis.md) | Coverage of each clause |

The feature trackers in `odd/tasks/` record the decisions, checks and
production deployments of each change.

## Development

- **Layout**: `app/` holds the routes, services, models, templates, static
  assets and the MCP server; `migrations/` the Alembic migrations; `tests/` the
  test suite; `celery_worker.py` the scheduled tasks; `wsgi.py` and
  `gunicorn.conf.py` the production entry point; the `iso9001*.service` files
  the systemd units.
- **Architecture**: writes go through a service layer independent of Flask
  (permissions, validation, attribution and audit log); the web and the MCP
  server are adapters over it. See
  [`docs/architecture/services.md`](docs/architecture/services.md).
- **Tests**: `venv/bin/python -m unittest discover -s tests -p 'test_*.py'`.
  The PostgreSQL tests run only with `TEST_POSTGRES_URI`; see
  [Continuous integration](docs/operations.md#continuous-integration).
- **User interface**: one stylesheet, `app/static/css/app.css`, with design
  tokens (black, bone white and canary yellow `#FFF000`) as CSS custom
  properties. Every asset is served locally, so the Content-Security-Policy
  allows no external origin: the Barlow Condensed, IBM Plex Sans and IBM Plex
  Mono fonts (SIL Open Font License, see `app/static/fonts/*-OFL.txt`) and
  Chart.js 4.5.1 (MIT, see `app/static/lib/chart.js-LICENSE.md`). Shared
  template pieces live in `app/templates/_partials/`. Forms marked with
  `data-confirm="…"` ask for confirmation through `app/static/js/app.js`; do
  not use inline `onclick` or `onsubmit` handlers.

## Continuous integration

GitHub Actions runs the `Python 3.11 tests` check, against PostgreSQL 17, on
every push, on pull requests targeting `main` and when started manually. See
[Continuous integration](docs/operations.md#continuous-integration).

## Licence

GNU General Public License v3.0; see [LICENSE](LICENSE).
