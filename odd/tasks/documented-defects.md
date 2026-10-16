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

- [x] **DF-1 — Dashboard audit data by permission.** Route: delegated (template, route, tests and two guides). Forecast 100-250.
- [x] **DF-2 — Monthly report generation date.** Route: delegated (report, notifications, tests, docs). Forecast 60-150.
- [x] **DF-3 — Permanent sessions and per-administrator reset limit.** Route: delegated (auth, config, rate limit, tests, docs). Forecast 120-300.
- [x] **DF-4 — Repository hygiene and closing.** Route: inline (two mechanical lines plus the gap-analysis defects table). Forecast 20-60.
  - `instance/` in `.gitignore`; the systemd `Documentation=` link points to `docs/operaciones.md`; every fixed defect marked "Corregido" in the gap analysis.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `999df34`: 1,329 tests (PostgreSQL via `TEST_POSTGRES_URI`).

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| DF-1 | Done | `32e4fdf` (`audits.upcoming(session, actor, *, today, within_days)` requiring `read` on audits, both window ends included, `COMPLETADA` and `CANCELADA` left out, ordered by date then id; the dashboard computes the audit count and the upcoming list only when `can('read', 'audits')`; card `#auditorias-proximas`; dashboard sections of both guides) | RED: `no attribute 'upcoming'` (2), OPERATIVO saw the audit label, areas and dates, upcoming count `3 != 5` with cancelled and completed audits, missing card. GREEN: 6 new tests; 1,335 tests OK incl. PostgreSQL | Range `999df34..32e4fdf`: **medium**, `under_budget` (pending in the slice) |
| DF-2 | Done | `5c2f18b` (`_monthly_report_context` resolves `today` once and returns it as `generado`, so the generation date and the objectives cutoff use the same day; the header shows "Periodo: <mes> de <año>"; the scheduled e-mail passes its run date; both guides) | RED: 3 new tests found "Generado el 30/09/2026" instead of the generation day (PDF, HTML and the scheduled e-mail). GREEN: 1,338 tests OK incl. PostgreSQL; adapted `tests/test_report_period.py` and `tests/test_scheduled_notifications.py` (standing approval for tests that encode the old behaviour) | Pending in the slice |
| DF-3 | Done | `cc1c4a5` (`session.permanent = True` after a successful `login_user`, so the cookie expires and the 8-hour lifetime is an idle timeout refreshed on each request; the four dead `REMEMBER_COOKIE_*` settings removed; `send_reset_link` limited per administrator with `key_func` `user:<id>`, still the innermost decorator; `docs/operaciones.md` and both guides) | RED: session cookie without expiry, no refreshed cookie, `REMEMBER_COOKIE_*` present; a second administrator behind the same address got `[429]*10` and one administrator from two addresses was not limited. GREEN: 1,346 tests OK incl. PostgreSQL; time tests shift `flask.sessions.datetime.now` and `itsdangerous.TimestampSigner.get_timestamp`; adapted `tests/test_user_account_actions.py` (reset-link limit cases). The profile credential limit was already per user | Pending in the slice |
| DF-4 | Done | `50d5397` (`instance/` in `.gitignore`; the four systemd units link `docs/operaciones.md` or `docs/mcp.md`; the gap analysis marks the four defects fixed and drops the "open defect" notes from 7.5.2, 7.5.3, 9.1.1, 9.2 and the roles section) | Structural readback; `git check-ignore` matches `instance/documents/…`; 1,346 tests OK (includes the documentation link test) | Pending in the slice |

## Production deployment (pending authorization)

No migration. Fast-forward and restart: the session change applies to new sign-ins (existing sessions keep their old cookie until it is rewritten); the systemd unit files changed only in `Documentation=`, so reinstalling them is optional (`sudo cp` plus `systemctl daemon-reload`, by the user). Smoke tests: sign-in sets a session cookie with an expiry; an OPERATIVO dashboard shows no audit card.

## Next step

Review the slice, deliver one pull request per task, then the production deployment above after explicit authorization.
