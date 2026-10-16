# Documented Defects

Repository locator: `odd/tasks/documented-defects.md` · Engram mirror: `odd/documented-defects/tasks`

## Objective

Fix the code defects found while writing the documentation (`odd/tasks/documentation-refresh.md`, findings) and listed as open in `docs/analisis-brechas-iso-9001.md` ("Defectos verificados"), and keep the documentation true after each fix.

## Problem and why it matters

- The dashboard shows audit figures and upcoming audits (area and date) to OPERATIVO, which has no read access to audits, and the upcoming card also lists cancelled and completed audits (`app/routes/dashboard_routes.py`, `app/templates/dashboard/dashboard.html`).
- The monthly report prints the month's last day as "Generado el" (`app/utils/reports.py`, `app/audit_notifications.py`): a dated quality record that states the wrong date (7.5.2, 9.1.1).
- Sign-in never makes the session permanent: the session cookie has no expiry date, the 8-hour lifetime only limits the cookie's age since its last write, and `REMEMBER_COOKIE_DURATION` is dead configuration (`app/routes/auth_routes.py`, `app/config.py`).
- The administrators' password-reset-link limit counts per network address, not per administrator.
- `instance/` (database files, uploaded documents) is not in `.gitignore`; the web systemd unit has a placeholder `Documentation=` link.

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `fix/documented-defects` in worktree `iso9001-worktrees/user-management` from `main@999df34` | User: "adelante con los defectos en el orden que consideres" (2026-10-16) |
| DD1 | Order by consequence: the permission leak first, then the wrong date on a quality record, then authentication, then repository hygiene | Assistant (authorized to choose the order) |
| DD2 | Dashboard: audit figures and cards are shown only to roles that can read audits; "upcoming" lists only audits that are still to be held (not cancelled or completed) | Assistant design, from the existing policy |
| DD3 | The monthly report's "Generado el" is the date it is generated (the adapter's `local_today()`), on the web PDF and on the e-mailed report | Assistant design |
| DD4 | Sessions become permanent at sign-in, so the cookie expires and the 8-hour `PERMANENT_SESSION_LIFETIME` acts as an idle timeout refreshed on each request; no "remember me" is offered, so the unused `REMEMBER_COOKIE_*` settings are removed | Assistant design (conservative: no new long-lived login) |
| DD5 | The reset-link limit is keyed on the requesting administrator, not on the network address | Assistant design |
| DD6 | Out of scope: screens for the MCP-only registers (roles, risks, processes, training resources, audit indicators) are Wave 2 work, not defect fixes | Assistant; gap analysis Wave 2 item 2 |
| Docs | Each fix updates the Spanish reference documents first (`docs/guia-de-usuario.md`, `docs/operaciones.md`, the defects table of `docs/analisis-brechas-iso-9001.md`), then `docs/user-guide.md` | Documentation language decision (2026-10-16) |
| Delivery | `auto-chain`, `stacked-to-main`: one pull request per task; push, PR and merge when CI is green; the production deployment needs explicit authorization | User authorization (2026-10-02) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

- [ ] **DF-1 — Dashboard audit data by permission.** Route: delegated (template, route, tests and two guides). Forecast 100-250.
- [ ] **DF-2 — Monthly report generation date.** Route: delegated (report, notifications, tests, docs). Forecast 60-150.
- [ ] **DF-3 — Permanent sessions and per-administrator reset limit.** Route: delegated (auth, config, rate limit, tests, docs). Forecast 120-300.
- [ ] **DF-4 — Repository hygiene and closing.** Route: inline (two mechanical lines plus the gap-analysis defects table). Forecast 20-60.
  - `instance/` in `.gitignore`; the systemd `Documentation=` link points to `docs/operaciones.md`; every fixed defect marked "Corregido" in the gap analysis.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `999df34`: 1,329 tests (PostgreSQL via `TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| DF-1..DF-4 | Pending | — | — | — |

## Next step

DF-1.
