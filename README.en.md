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
- **Context, interested parties and scope** (4.1, 4.2, 4.3): external and
  internal issues with their relevance and that of climate change, cited by
  risks; interested parties with type, relevance, owner, next review and
  climate-related requirements; the QMS scope as approved versions, with the
  justified applicability of each requirement and its PDF.
- **Processes** (4.4, 8.1): type, owner, inputs, outputs, resources,
  operating and acceptance criteria, related processes and the records citing
  each process.
- **People and competence** (5.3, 7.2): people, QMS roles with their
  responsibilities, authorities and reporting line, required and demonstrated
  competence, the competence matrix.
- **Nonconformities and corrective actions** (8.7, 10.2): output disposition,
  concessions with authority, date and justification, linked processes,
  containment, root cause, corrective actions, effectiveness verification,
  closing and reopening.
- **Read confirmations** (5.2, 7.3): dated evidence per person for a specific
  document revision or the quality objectives, with person history, web screens
  and MCP. Every role reads and writes; only administrators delete on the web.
- **Document control** (7.5): numbered revisions reviewed, approved and
  published, attachments, withdrawal and periodic review.
- **Internal audits** (9.2): yearly audit programme, audits with independence
  checks, classified findings that open nonconformities.
- **Risks and opportunities** (6.1): separate registers with assessment,
  residual assessment, a heat map, actions with their effectiveness
  evaluation, overdue reviews and PDFs.
- **Quality objectives** (6.2): indicator, target, owner, measurements and
  trend.
- **Changes to the QMS** (6.3): planned changes with their origin, the
  considerations a) to d), communication, affected processes and documents,
  tasks, approval by an administrator, implementation, effectiveness review,
  dashboard alerts, register and plan PDFs, and a management review input.
- **External providers** (8.4, 9.1.3): selection criteria, affected processes,
  approval by an administrator, scored evaluations with the next evaluation,
  linked nonconformities and the approved-providers PDF.
- **Customers, complaints and satisfaction** (8.2, 9.1.2): customer master data
  with unique names and optional tax IDs, complaints with responses, closure,
  cancellation and administrator reopening, optional nonconformity links, and
  surveys linked to customers with a survey type and contact channel. Complaint
  transitions are web-only; MCP creates and updates ordinary fields.
- **Management review** (9.3): inputs drawn from the registers, conclusions,
  actions and the minutes PDF.
- **Improvements** (10.1, 10.3): origin links, owner, expected benefit,
  approval, actual implementation date and independent effectiveness review.
  Every role can record and edit; lifecycle transitions are web-only.
- **Earlier registers**: training resources (7.1), training (7.2) and audit
  indicators (9.1), all with screens.
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
