# Documentation Refresh

Repository locator: `odd/tasks/documentation-refresh.md` · Engram mirror: `odd/documentation-refresh/tasks`

## Objective

Bring the documentation in line with the delivered system: a short English README, an English operations guide, a user guide in English and in Spanish, and a gap analysis re-assessed against the delivered Wave 1.

## Problem and why it matters

- `README.md` (879 lines) mixes languages (the original Spanish text, and sections 2b–2i with Spanish headings over English bodies), its table of contents does not match its content, it has broken Markdown (escaped headings such as `\###`), and it holds installation, a guide per module, operations and deployment in one file.
- `docs/iso-9001-gap-analysis.md` reflects only clause 9.3; every other clause row still describes the code before Wave 1 (for example 7.5.3 is "not covered" although document control is in production).
- Code, comments, commits, `docs/` and the `odd/` trackers are already in English; the interface is in Spanish and the guide's readers include quality staff and external auditors.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `docs/documentation-refresh` in worktree `iso9001-worktrees/user-management` from `main@3cbef71` | User: "antes de seguir con las mejoras deberíamos actualizar la documentación" (2026-10-16) |
| DR1 | Technical documentation in English: README, operations guide, `docs/mcp.md`, `docs/architecture/services.md`, gap analysis | Assistant recommendation; user did not object (2026-10-16) |
| DR2 | User guide in both English (`docs/user-guide.md`) and Spanish (`docs/guia-de-usuario.md`, neutral professional Spanish from Spain, no voseo); both cover the same content and link to each other | User: "ambas a ser posible" (2026-10-16) |
| DR3 | Nothing is lost when the README shrinks: every instruction moves to the operations or user guide, or is dropped only when it is wrong or obsolete (listed in the task's report) | Assistant design |
| DR4 | Documentation only: no application code changes; tests change only where they pin documentation text | Assistant design |
| Delivery | `auto-chain`, `stacked-to-main`: one pull request per task; push, PR and merge when CI is green; ask before a pull request above 1,500 changed lines | User authorization (2026-10-02) |
| RDD | On (global); assess every work-unit commit (documentation is expected to be passive) | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct** (each needs reading beyond the inline budget).

- [x] **DOC-1 — README and operations guide.** Forecast 900-1,400 (mostly moved lines).
  - Short English `README.md` (what it is, ISO 9001:2026 scope, quick start, links); `docs/operations.md` (requirements, installation, configuration and environment variables, database and migrations, Redis and Celery, Gunicorn, Nginx and systemd, the production deployment procedure used for `vulcano` without host names or secrets, backups including `instance/documents`, restore, monitoring, security notes, CI); fix tests that pin README text.
- [x] **DOC-2 — English user guide.** Forecast 600-1,000.
  - `docs/user-guide.md`: roles and access, dashboard, every module (users and profile, people and competence, nonconformities and corrective actions, document control, audit programme and findings, quality objectives, management review, and the older registers), PDFs and the monthly report, API tokens for agents (pointing to `docs/mcp.md`).
- [ ] **DOC-3 — Spanish user guide.** Forecast 600-1,000.
  - `docs/guia-de-usuario.md`: the same content as DOC-2, using the interface's Spanish labels.
- [ ] **DOC-4 — Gap analysis and technical docs review.** Forecast 200-500.
  - Re-assess every clause row of `docs/iso-9001-gap-analysis.md` against the code at the branch head with file references; check `docs/mcp.md` and `docs/architecture/services.md` against the code.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Plus a relative-link check over the Markdown files (every relative link and anchor resolves). Baseline at `3cbef71`: 1,327 tests.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| DOC-1 | Done | `6957ec7` (`docs/operations.md`, 651 lines; `docs/mcp.md` links its API-token section), `9b7e801` (English `README.md`, 879 → 120 lines) | Structural readback; no secrets or hosts; relative-link check: 57 links in 5 files, 0 errors (5 expected-missing links to the DOC-2 and DOC-3 guides); 1,327 tests OK | `3cbef71..9b7e801`: **passive**, no review |
| DOC-2 | Done | `b6be86f` (`docs/user-guide.md`, 1,039 lines: sign-in and profile, screens, roles and access matching `policy.py`, dashboard, every module with workflow, states, refusals and PDFs, other registers, reports and e-mails, glossary) | Structural readback; relative-link check: 91 links in 6 files, 0 errors (2 expected-missing links to DOC-3); 1,327 tests OK | Passive (documentation only) |
| DOC-3..DOC-4 | Pending | — | — | — |

## Findings during implementation

- **Old README destinations (DR3):** installation, configuration, database, Celery, maintenance, security, deployment and Nginx moved to `docs/operations.md`; the module sections 2b–2i, the Celery e-mail contents and the dashboard checks are kept verbatim for DOC-2 and DOC-3 in the job scratch file `readme-module-sections.md`; the project tree was dropped as obsolete (no services, MCP or newer routes).
- **Wrong claims corrected:** the Gunicorn entry point is `wsgi:app`, not `app:app`; migrations ship with the repository, so setup runs no `flask db init` or `migrate`; Flask 3 does not read `FLASK_ENV`; `pip install -U` changes nothing with pinned requirements.
- **Code gaps found (not in this feature's scope, reported to the user):** sign-in calls `login_user(user)` without a permanent session or remember cookie, so `PERMANENT_SESSION_LIFETIME` and `REMEMBER_COOKIE_DURATION` have no effect; `instance/` is not in `.gitignore`; the web systemd unit has a placeholder `Documentation=` link.
- **Old material vs code (DOC-2 follows the code):** the nonconformity's free-text **Acción Correctiva** is still editable on its forms; in the web interface a management review's conclusions are written only once it is held (**Comentarios y conclusiones**), while the MCP server accepts them on a draft.
- **More code gaps found while documenting (reported to the user):** the dashboard shows the audit count and upcoming audits (area and date) to Operativo, which has no audit read access, and that card also lists cancelled and completed audits; the monthly report PDF prints the month's last day as "Generado el" (`app/utils/reports.py` passes `generado=fecha`); roles, risks and opportunities, training resources, processes and audit indicators have no web screens (only MCP or the JSON endpoints) although people, competence and objectives pick from them; the administrators' reset-link limit counts per network address, not per administrator.
- `gunicorn.conf.py` sets `preload_app = True`, so deployments restart `iso9001.service` (a HUP does not load new code); the units use `ProtectSystem=strict`, so a `DOCUMENT_STORAGE_DIR` outside the checkout needs its own `ReadWritePaths`.

## Delivery

| Slice | Pull request | Commits | Merged as | Note |
|---|---|---|---|---|
| 1 | [#118](https://github.com/constant1n0/iso9001/pull/118) | tracker, operations guide | merged | |
| 2 | [#119](https://github.com/constant1n0/iso9001/pull/119) | README, record | `9b8d96a` | |
| 3 | Pending | English user guide, this record | — | |

## Next step

DOC-3.
