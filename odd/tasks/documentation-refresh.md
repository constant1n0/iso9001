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
| DR1 | **Revised 2026-10-16.** Spanish is the reference language for everything companies, users and deployers read: `README.md`, the user guide (`docs/guia-de-usuario.md`), the operations guide (`docs/operaciones.md`), the MCP guide (`docs/mcp.md`) and the ISO 9001 gap analysis (`docs/analisis-brechas-iso-9001.md`); neutral professional Spanish from Spain, no voseo. The first version of this decision (all technical documentation in English) is superseded: DOC-1 wrote the README and operations guide in English, DOC-5 and DOC-6 translate them | User: "El español manda… nuestro objetivo son empresas españolas o en España"; "sí, adelante con ese reparto" (2026-10-16) |
| DR2 | English is kept minimal for international visitors: a short `README.en.md` and `docs/user-guide.md`, marked as translations of the Spanish reference (a change is written in Spanish first). Developer material stays in English: code, comments, commits, `docs/architecture/services.md` and the `odd/` trackers | Same; full bilingual copies of every document rejected to avoid double maintenance and drift |
| DR2a | The repository is also a showcase for Spanish companies: the Spanish README presents the product (follow-up: screenshots, GitHub description and topics, a demo) | User (2026-10-16) |
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
- [x] **DOC-3 — Spanish user guide.** Forecast 600-1,000.
  - `docs/guia-de-usuario.md`: the same content as DOC-2, using the interface's Spanish labels.
- [x] **DOC-4 — Gap analysis and technical docs review.** Forecast 200-500.
  - Re-assess every clause row of `docs/iso-9001-gap-analysis.md` against the code at the branch head with file references; check `docs/mcp.md` and `docs/architecture/services.md` against the code.
- [x] **DOC-5 — Spanish README and English summary.** Forecast 250-400.
  - `README.md` in Spanish as the showcase entry point; `README.en.md` short with links; `docs/user-guide.md` gets a note that the Spanish guide is the reference.
- [x] **DOC-6 — Spanish operations guide.** Forecast 1,300 (rename plus translation).
  - `docs/operations.md` → `docs/operaciones.md`, translated; every link updated.
- [x] **DOC-7 — Spanish MCP guide.** Forecast 1,000-1,100 (translation in place).
  - `docs/mcp.md` translated; tests that pin its text adjusted to the Spanish wording with the same intent.
- [x] **DOC-8 — Spanish gap analysis.** Forecast 400-700, after DOC-4.
  - `docs/iso-9001-gap-analysis.md` → `docs/analisis-brechas-iso-9001.md`, translated; every link updated.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Plus a relative-link check over the Markdown files (every relative link and anchor resolves). Baseline at `3cbef71`: 1,327 tests.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| DOC-1 | Done | `6957ec7` (`docs/operations.md`, 651 lines; `docs/mcp.md` links its API-token section), `9b7e801` (English `README.md`, 879 → 120 lines) | Structural readback; no secrets or hosts; relative-link check: 57 links in 5 files, 0 errors (5 expected-missing links to the DOC-2 and DOC-3 guides); 1,327 tests OK | `3cbef71..9b7e801`: **passive**, no review |
| DOC-2 | Done | `d14aa4a` (`docs/user-guide.md`, 1,039 lines: sign-in and profile, screens, roles and access matching `policy.py`, dashboard, every module with workflow, states, refusals and PDFs, other registers, reports and e-mails, glossary) | Structural readback; relative-link check: 91 links in 6 files, 0 errors (2 expected-missing links to DOC-3); 1,327 tests OK | Passive (documentation only) |
| DOC-3 | Done | `c416a19` (`docs/guia-de-usuario.md`, 1,055 lines, the same 58 headings as the English guide, "usted" in the prose, ISO clauses as "apartado", a "Términos" section of 17 terms) | Structural readback; 420 bold labels checked by script against the templates and the Python sources; relative-link check: 127 links in 7 files, 0 errors, none missing; 1,327 tests OK | Passive (documentation only) |
| DOC-4 | Done | `538200e` (gap analysis re-assessed at `fd46fad`: 34 clause rows now 9 covered, 16 partial, 6 not covered, 3 documentary only (before: 1 / 21 / 8 / 4); changed rows 6.2, 7.2, 7.5.1, 7.5.2, 7.5.3, 9.1.1, 9.2, 10.2 → covered, 6.3 and 7.3 → partial; Wave 2 proposed; `services.md`: MCP no longer "future", people and competence in the module table and permission matrix, channel and token rules, users have screens, `document_files.store` signature; `mcp.md`: systemd steps and stdio deactivation) | Structural readback; relative-link check: 131 links in 7 files, 0 errors; 1,327 tests OK | Passive (documentation only) |
| DOC-5 | Done | `d440eaf` (Spanish `README.md` as the showcase entry point, 173 lines, with a screenshot placeholder comment; `README.en.md`, 62 lines; `docs/user-guide.md` marked as a translation) | Structural readback; pinned README strings kept; link check: only the DOC-6 and DOC-8 targets missing until those tasks; 1,327 tests OK | Passive |
| DOC-6 | Done | `61f30fc` (pure rename `docs/operations.md` → `docs/operaciones.md`, so `git log --follow` keeps the history), `f3c85de` (Spanish translation, 707 lines; every link and anchor updated; the Spanish guide's intro now lists only `services.md` as English) | 23 code blocks byte-identical, 306 inline code spans and 43 table rows kept; link check: only the DOC-8 target missing; 1,327 tests OK | Passive |
| DOC-7 | Done | `84ec8fb` (`docs/mcp.md` translated in place, 586 lines, contents list; inbound anchors updated; `tests/test_mcp_deployment.py` pins the Spanish headings "## Prueba de humo" and "### Estado de la verificación", splitting on the full heading line) | 13 code blocks byte-identical (same sha256); link check: only the DOC-8 target missing; 1,327 tests OK | Range `origin/main..HEAD` (3,167 lines) refused with `lens_context_budget_exceeded`; reviewed alone in a detached worktree as `f3c85de..84ec8fb` (822 lines): **medium** (the test change), consent granted, reliability review `review-34f5b916875c5032` **approved** and acknowledged; its suggestion applied in `c27da4e` (`tests/test_documentation_links.py` checks every relative link and anchor in the READMEs and `docs/`); the other slices are passive |
| DOC-8 | Done | `f82851d` (pure rename to `docs/analisis-brechas-iso-9001.md`), `ba4808b` (Spanish translation with the Spanish edition's terms; clause titles from the 2015 Spanish wording because the 2026 Spanish text could not be checked; "Wave" → "fase") | 3 Mermaid blocks byte-identical; 385 of 386 inline code spans unchanged (the `[VERIFY: …]` marker became `[VERIFICAR: …]`); 34 clause rows keep numbers and status codes; link check: 163 links, 0 errors; 1,327 tests OK | Passive |

## Findings during implementation

- **Old README destinations (DR3):** installation, configuration, database, Celery, maintenance, security, deployment and Nginx moved to `docs/operations.md`; the module sections 2b–2i, the Celery e-mail contents and the dashboard checks are kept verbatim for DOC-2 and DOC-3 in the job scratch file `readme-module-sections.md`; the project tree was dropped as obsolete (no services, MCP or newer routes).
- **Wrong claims corrected:** the Gunicorn entry point is `wsgi:app`, not `app:app`; migrations ship with the repository, so setup runs no `flask db init` or `migrate`; Flask 3 does not read `FLASK_ENV`; `pip install -U` changes nothing with pinned requirements.
- **Code gaps found (not in this feature's scope, reported to the user):** sign-in calls `login_user(user)` without a permanent session or remember cookie, so the session cookie has no expiry date and `REMEMBER_COOKIE_DURATION` is unused (corrected by DOC-4: Flask still rejects a session 8 hours after the cookie was last written, because it applies `PERMANENT_SESSION_LIFETIME` as a maximum age to every signed session cookie); `instance/` is not in `.gitignore`; the web systemd unit has a placeholder `Documentation=` link.
- **Old material vs code (DOC-2 follows the code):** the nonconformity's free-text **Acción Correctiva** is still editable on its forms; in the web interface a management review's conclusions are written only once it is held (**Comentarios y conclusiones**), while the MCP server accepts them on a draft.
- **More code gaps found while documenting (reported to the user):** the dashboard shows the audit count and upcoming audits (area and date) to Operativo, which has no audit read access, and that card also lists cancelled and completed audits; the monthly report PDF prints the month's last day as "Generado el" (`app/utils/reports.py` passes `generado=fecha`); roles, risks and opportunities, training resources, processes and audit indicators have no web screens (only MCP or the JSON endpoints) although people, competence and objectives pick from them; the administrators' reset-link limit counts per network address, not per administrator.
- `gunicorn.conf.py` sets `preload_app = True`, so deployments restart `iso9001.service` (a HUP does not load new code); the units use `ProtectSystem=strict`, so a `DOCUMENT_STORAGE_DIR` outside the checkout needs its own `ReadWritePaths`.

## Delivery

| Slice | Pull request | Commits | Merged as | Note |
|---|---|---|---|---|
| 1 | [#118](https://github.com/constant1n0/iso9001/pull/118) | tracker, operations guide | merged | |
| 2 | [#119](https://github.com/constant1n0/iso9001/pull/119) | README, record | `9b8d96a` | |
| 3 | [#120](https://github.com/constant1n0/iso9001/pull/120) | English user guide, record | `c3c6ee2` | |
| 4 | [#121](https://github.com/constant1n0/iso9001/pull/121) | Spanish user guide, record | `3cd4e78` | |
| 5 | [#122](https://github.com/constant1n0/iso9001/pull/122) | language revision, DOC-4, record | `8cc3a98` | |
| 6 | Pending | DOC-5 Spanish README | — | Merged back to back with 7–9 so `main` keeps no broken link for long |
| 7 | Pending | DOC-6 Spanish operations guide | — | |
| 8 | Pending | DOC-7 Spanish MCP guide | — | |
| 9 | Pending | DOC-8 Spanish gap analysis, link test, closing record | — | Final slice |

## Next step

**Feature complete** once slices 6–9 merge. Follow-ups outside this feature: the code gaps listed in the findings (dashboard audit data shown to Operativo, the monthly report's "Generado el" date, registers without screens, session cookie expiry and remember-me, the reset-link limit per address, `instance/` in `.gitignore`, the systemd `Documentation=` placeholder); the showcase work (screenshots, GitHub description and topics, a demo); and the Wave 2 proposed in the gap analysis.
