# ISO 9001:2026 Label and Report Fixes

Repository locator: `odd/tasks/iso2026-and-report-fixes.md` · Engram mirror: `odd/iso2026-and-report-fixes/tasks`

## Objective

Ship a small release that shows the current edition of the standard in the UI, fixes the two wrong report figures found by the gap analysis, and lets the token CLI find a user by email.

## Problem and why it matters

- The sidebar footer says "Cláusulas ISO 9001:2015" (`app/templates/base.html:71`); ISO 9001:2026 was published on 2026-09-16 with the same clause structure (4 to 10).
- The monthly quality report counts all-time totals instead of the reported month (`app/utils/reports.py`, `_monthly_report_context`); it is e-mailed to administrators on the 1st of each month.
- The dashboard satisfaction chart groups by calendar month only, so January 2025 and January 2026 are averaged together (`app/routes/dashboard_routes.py`).
- `flask create-api-token --user` (and `revoke`/`list` filters) only match the username; the administrator tried an email address and got "User not found".

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer; branch `fix/iso2026-label-and-reports` in worktree `iso9001-worktrees/fixes-2026` from `main@5a39191` | User "ok" to the proposed small release (2026-10-04) |
| F1 | UI label shows ISO 9001:2026; the 2026 content changes (risks vs opportunities, change management) stay in the roadmap | Assistant proposal accepted |
| F2 | The monthly report covers the calendar month of the given date (all counts and the satisfaction average) | Gap-analysis defect |
| F3 | The dashboard satisfaction chart groups by year and month, last 12 months, labelled `YYYY-MM` (or the existing label style with the year) | Gap-analysis defect |
| F4 | Token CLI `--user` accepts a username or an email address (case-insensitive email match) | Production incident while issuing the first token |
| Delivery | One pull request if under about 400 changed lines; merge when CI is green; deploy to `vulcano` with the same procedure (no migrations, no new services; the user restarts web and Celery) | User "ok" (2026-10-04) |

## Scope

Included: F1–F4 with tests. Excluded: user management (separate feature), Flask-Limiter storage backend, any schema change.

## Tasks

Route: **delegated direct** (several non-trivial files with tests).

- [x] **FX-1 — ISO 9001:2026 label.** Acceptance: the sidebar footer reads "Cláusulas ISO 9001:2026"; no other "2015" reference remains in `app/`.
- [x] **FX-2 — Monthly report covers its month.** Acceptance: records outside the month are excluded from every figure; the satisfaction average uses only the month's surveys; the PDF/HTML render still works.
- [x] **FX-3 — Satisfaction chart by year and month.** Acceptance: two surveys in January of different years produce two points; at most the last 12 months; labels distinguish years.
- [x] **FX-4 — Token CLI accepts email.** Acceptance: `--user` matches a username or an email (case-insensitive); ambiguity is impossible because both columns are unique; the error message names what was tried without leaking other accounts.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `5a39191`: 531 tests. RDD on: assess each work-unit commit.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| FX-1 | Done | `47aa7a9` | RED: 2 `test_iso_edition_label` tests (2015 string in `base.html`). GREEN | Range `5a39191..94775fc`: **medium**, `under_budget`; standalone release, so it stays unreviewed by RDD; writer self-verification plus parent full-suite run |
| FX-2 | Done | `3d26b48` | RED: 3 `test_report_period` tests (`2 != 5`, empty month not zero, e-mail not reporting the previous month). GREEN | Same range |
| FX-3 | Done | `e3b5c24` | RED: 2 `test_dashboard_satisfaction_chart` tests (`[10, 11]` instead of `["2025-11", "2026-10"]`). GREEN; `test_ui_foundation` now pins `local_today` so the 12-month window cannot drift out of its seeded data | Same range |
| FX-4 | Done | `94775fc` | RED: 2 CLI tests ("User ... not found" for an email). GREEN | Same range |

Full suite after `94775fc`: `Ran 541 tests`, `OK` (531 + 10), including PostgreSQL.

## Findings during implementation

- The monthly e-mail runs at 08:00 on the 1st (`0 8 1 * *`), so `send_monthly_quality_report` now reports the previous calendar month, dated its last day; the PDF name follows that month. On-demand report exports cover the month of the given date.
- The dashboard chart data contract changed: `satisfaccion.meses` holds `"YYYY-MM"` strings and the chart shows labels like "Oct 2025".
- The monthly report copy now says the figures are those recorded in the report's month.
- The administrator's username in production is their e-mail address; the failed attempt used a mistyped address, which the email lookup would not have fixed either.

## Next step

Push, open the pull request, merge when CI is green, then redeploy to `vulcano` (no migrations; the user restarts web and Celery).
