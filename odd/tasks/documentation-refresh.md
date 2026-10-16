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

- [ ] **DOC-1 — README and operations guide.** Forecast 900-1,400 (mostly moved lines).
  - Short English `README.md` (what it is, ISO 9001:2026 scope, quick start, links); `docs/operations.md` (requirements, installation, configuration and environment variables, database and migrations, Redis and Celery, Gunicorn, Nginx and systemd, the production deployment procedure used for `vulcano` without host names or secrets, backups including `instance/documents`, restore, monitoring, security notes, CI); fix tests that pin README text.
- [ ] **DOC-2 — English user guide.** Forecast 600-1,000.
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
| DOC-1..DOC-4 | Pending | — | — | — |

## Next step

DOC-1.
