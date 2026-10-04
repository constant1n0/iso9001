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

- [ ] **FX-1 — ISO 9001:2026 label.** Acceptance: the sidebar footer reads "Cláusulas ISO 9001:2026"; no other "2015" reference remains in `app/`.
- [ ] **FX-2 — Monthly report covers its month.** Acceptance: records outside the month are excluded from every figure; the satisfaction average uses only the month's surveys; the PDF/HTML render still works.
- [ ] **FX-3 — Satisfaction chart by year and month.** Acceptance: two surveys in January of different years produce two points; at most the last 12 months; labels distinguish years.
- [ ] **FX-4 — Token CLI accepts email.** Acceptance: `--user` matches a username or an email (case-insensitive); ambiguity is impossible because both columns are unique; the error message names what was tried without leaking other accounts.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `5a39191`: 531 tests. RDD on: assess each work-unit commit.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| FX-1..FX-4 | Pending | — | — | — |

## Next step

Implement FX-1..FX-4, then review, deliver and deploy.
