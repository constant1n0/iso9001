# ISO 9001 QMS

[Versión en español](README.md)

This is a short English summary. The Spanish documentation is the reference:
changes are written in Spanish first, and the full README, the operations
guide, the MCP guide and the ISO 9001 gap analysis are in Spanish.

## What it is

A quality management system (QMS) for ISO 9001:2026, built on Flask,
PostgreSQL, Celery and Redis. People work in a Spanish web interface; AI
agents work on the same records through an MCP server, as a user, under the
same permissions, with revocable API tokens. Every change goes through one
service layer that checks permissions, validates, attributes and writes the
audit log. Users are deactivated and documents withdrawn, never deleted, and
an agent can never delete anything.

## Modules

- **Dashboard**: key figures, charts and alerts.
- **Users and profile** (clause 5.3): the *Administrador*, *Auditor* and
  *Operativo* roles, deactivation instead of deletion, profile and API tokens.
- **People and competence** (5.3, 7.2): people, QMS roles, required and
  demonstrated competence, the competence matrix.
- **Nonconformities and corrective actions** (10.2): containment, root cause,
  corrective actions, effectiveness verification, closing and reopening.
- **Document control** (7.5): numbered revisions reviewed, approved and
  published, attachments, withdrawal and periodic review.
- **Internal audits** (9.2): yearly audit programme, audits with independence
  checks, classified findings that open nonconformities.
- **Quality objectives** (6.2): indicator, target, owner, measurements and
  trend.
- **Management review** (9.3): inputs drawn from the registers, conclusions,
  actions and the minutes PDF.
- **Earlier registers**: interested parties (4.2), processes (4.4, 8.1), roles
  (5.3), risks and opportunities (6.1), training resources (7.1), training
  (7.2), audit indicators (9.1), customer satisfaction (9.1.2) and
  improvements (10.3).
- **Reports and notifications** (9.1): PDFs of the records, a monthly quality
  report and scheduled e-mail alerts.

## Documentation

- [User guide](docs/user-guide.md): the English translation of the Spanish
  user guide.
- In Spanish (the reference):
  - [Guía de usuario](docs/guia-de-usuario.md): the user guide;
  - [README](README.md): presentation, quick start and development;
  - [Guía de operaciones](docs/operaciones.md): installation, configuration,
    deployment, backups and CI;
  - [Servidor MCP](docs/mcp.md): API tokens, the MCP server and AI-agent
    clients;
  - [Análisis de brechas ISO 9001](docs/analisis-brechas-iso-9001.md):
    coverage of each clause.
- In English, for developers:
  [service layer](docs/architecture/services.md) (architecture, permission
  matrix, state rules, writing an adapter).

## Licence

GNU General Public License v3.0; see [LICENSE](LICENSE).
