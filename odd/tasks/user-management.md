# User Management

Repository locator: `odd/tasks/user-management.md` · Engram mirror: `odd/user-management/tasks`

## Objective

Let an administrator manage the people who use the QMS from the web, and let every user manage their own account, without ever deleting a user, so the audit trail stays attributable.

## Problem and why it matters

- Users can only be created with `flask create-admin`, which refuses as soon as one user exists; there is no way to add colleagues, change a role or take access away (ISO 9001 clauses 5.3 and 7.2 need assigned roles and controlled access).
- A user cannot see their own username, change their e-mail or password, or see the API tokens issued to them; the first production administrator did not know their username.
- `User` has no state: `UserMixin.is_active` is always true, so access cannot be withdrawn without deleting the row, which would orphan attribution (`SET NULL`) and silently drop the user's API tokens (`CASCADE`).

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, delegated direct writers, one at a time; branch `feat/user-management` in worktree `iso9001-worktrees/user-management` from `main@fc8c913` | User request (2026-10-04) |
| U0 | Scope "Completa": admin lists, creates (with role), edits e-mail and role, deactivates and reactivates, and sends a password-reset link; every user has "Mi perfil" to see their data, change their e-mail and password, and see and revoke their own API tokens. Token creation stays CLI-only (api-tokens T1) | User choice (2026-10-04), Engram #10832 |
| U1 | New column `users.active` (not null, default true). An inactive user cannot log in, loses existing sessions and remember cookies (`user_loader`), cannot request or use a reset link, is skipped by e-mail notifications, and cannot authenticate with an API token | Assistant design |
| U2 | Users are never deleted; `USERS` delete stays NOBODY. Deactivation also revokes the user's active API tokens, so the audit log records it | Assistant design |
| U3 | The `USERS` grant is unchanged: ADMIN and AUDITOR read (auditors see who holds each role), only ADMIN writes | Existing policy |
| U4 | Guard rails: nobody can deactivate or change the role of their own account, and the last active administrator can never be deactivated or demoted | Assistant design |
| U5 | The administrator creates a user with username, e-mail, role and an initial password (at least 8 characters, confirmed); usernames cannot be edited afterwards, to keep audit labels stable | Assistant design |
| U6 | E-mail addresses are stored trimmed and lower-cased and are unique case-insensitively; e-mail is required for new users | Assistant design |
| U7 | Changing your own e-mail or password requires your current password; new passwords need at least 8 characters | Assistant design |
| U8 | Password changes are audited with a non-sensitive marker (`credential_changed`) and written to the security log; the hash never appears in either | Assistant design |
| U9 | "Send reset link" reuses the existing reset e-mail (moved out of the routes module), with wording that fits an administrator-triggered send, a rate limit, and clean Spanish errors when the user has no e-mail, is inactive, or mail fails | Assistant design |
| U10 | "Mi perfil" lists only the signed-in user's tokens and revokes only those; another user's token answers "not found"; the `mcp` channel can never manage tokens | Assistant design |
| U11 | Out of scope: login by e-mail, last-login tracking, full names, forced password change on first login, a `create-user` CLI | Assistant design |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green, as in the previous features. Production needs `flask db upgrade` for U1, so its deployment is a separate, explicitly authorized step | User authorization (2026-10-02) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Scope

### Included

- Migration and model column `users.active`, and its enforcement in every authentication path.
- `app/services/users.py` with the guard rails, audit rows and token revocation.
- Administrator screens and navigation.
- "Mi perfil" with e-mail, password and own-token management.
- Tests, characterization table, and docs.

### Excluded

See U11. Also excluded: Flask-Limiter storage backend, `qms-people` competence records.

## Constraints

- Same as previous features: Python 3.11 in CI (worktree `venv` uses 3.12), stdlib `unittest`, strict test-first, Alembic migrations for PostgreSQL 17, services never import Flask, routes never write to the session directly (`tests/test_route_write_guard.py`), strict CSP (no inline scripts, handlers or styles), Spanish UI copy and English code and docs, conventional commits without AI attribution, about 400 changed lines per work unit as a planning heuristic.
- `User` is not in `AUDITED_MODELS`, so the service calls `audit.record` explicitly; the recorder drops keys containing `password`, `token` or `secret`.

## Tasks

Route for every task: **delegated direct** (each touches two or more non-trivial files).

- [x] **UM-1 — Account state.** Forecast 200-320.
  - Migration on top of `e6b1a4c8d3f7` adding `users.active` (server default true) and the model column; `is_active` returns it.
  - Refusal for inactive users in `user_loader`, login (generic "Credenciales inválidas" message, distinct security-log reason), reset request and reset, `api_tokens.authenticate` (new failure reason), and e-mail notification recipients.
  - Acceptance: an inactive user cannot log in, an existing session is dropped on the next request, no reset e-mail is sent, a valid token is refused, and notifications skip them; the migration upgrades and downgrades on PostgreSQL.
- [x] **UM-2 — Users service.** Forecast 350-450.
  - `app/services/users.py`: `list_`, `get`, `create`, `update` (e-mail, role), `set_active` (revokes active tokens on deactivation), `change_own_email`, `change_own_password`; shared password and e-mail validators; guard rails U4; audit rows and security log U8.
  - Move the reset e-mail helper to a shared module and add the administrator-triggered variant (U9).
  - Acceptance: policy first; duplicate username or e-mail is a `Conflict`; the last-admin and self-change rules hold; no-op updates write nothing; deactivation revokes tokens; nothing sensitive is audited.
- [x] **UM-3a — Administrator screens: list, create, edit.** Forecast 350-450.
  - Blueprint `users` with "Usuarios" list, new and edit screens over `users.list_`, `create`, `update`; forms; an "Administración" navigation group gated with `can()`.
  - Characterization table and policy tests extended for the new routes.
  - Acceptance: ADMIN manages, AUDITOR only reads, OPERATIVO is refused; guard-rail and validation errors show as Spanish messages without losing the form.
- [x] **UM-3b — Administrator account actions.** Forecast 250-350.
  - Deactivate, reactivate and send-reset POST actions on the list and edit screens; the reset e-mail helper moves out of `auth_routes.py` with wording for an administrator-triggered send; the reset request looks e-mail up regardless of case; security log for administrator actions and credential changes.
  - Acceptance: guard rails surface as Spanish flash messages; the reset action is rate limited and fails cleanly without an e-mail address, for an inactive user, or when mail fails; deactivation ends the user's sessions and tokens.
- [x] **UM-4 — Mi perfil.** Forecast 300-400.
  - Profile page reachable from the user chip: username, e-mail, role; forms to change e-mail and password (current password required); own API tokens with status and a revoke button.
  - `api_tokens` gains own-token listing and revocation for the web channel (U10).
  - Docs: README and `docs/architecture/services.md`.
  - Acceptance: every role can use it; a user cannot see or revoke another user's token; wrong current password changes nothing.

**Total forecast:** about 1,200-1,600 authored changed lines, so delivery is chained. UM-3 was split into UM-3a and UM-3b after UM-2 needed a size exception (921 lines).

## Checks

```bash
$ export TEST_POSTGRES_URI="postgresql://postgres:DB_PASSWORD@127.0.0.1:55432/iso_test"
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

`DB_PASSWORD` comes from the `iso-pg-test` container environment and is never printed or committed.

- Baseline at `fc8c913`: 541 tests.
- After each work-unit commit: `gentle-ai review assess --cwd <worktree> --agent claude-code --base-ref <last reviewed boundary> --committed-only --json`; the first boundary is the branch point `fc8c913`.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| UM-1 | Done | `21e6221` (column, migration `f2c7a9e4b1d6`, refusals), review follow-up test in the next commit | RED: 9 `test_user_account_state` tests (`'active' is an invalid keyword argument`), then 7 for the right reasons (no security-log warning, `302 != 200`, session still valid); migration `KeyError: 'active'`; notifications `1 != 2`; token of an inactive owner not refused. GREEN: 553 tests incl. PostgreSQL | Range `fc8c913..21e6221`: **high** (authentication, security log); consent granted; 4-lens review `review-97c3950522aa2f95` **approved** and acknowledged; no blocking findings; 3 suggestions applied (raw-insert migration test, this progress row, deploy order below) |
| UM-2 | Done | `0012c45` (validators, `api_tokens.revoke_all_for_user`), `81dbea9` (users service), review follow-up tests in the next commit | RED: 34 SQLite tests (`cannot import name 'users'`, missing `fields.email`/`new_password`, missing `revoke_all_for_user`); the PostgreSQL race test failed with the row lock removed. GREEN: 591 tests incl. PostgreSQL | Range `c767641..81dbea9`: **medium**, `slice_budget_reached` (900 lines, about 540 of them tests); consent granted; reliability review `review-7bbe74f4ae599a28` **approved** and acknowledged; 2 suggestions applied as tests (case-only e-mail change normalises a legacy address; a duplicate that slips past the pre-check hits the unique constraint and becomes a `Conflict`) |
| UM-3a | Done | `bcc196d` | RED: 15 `test_user_routes` tests (26 failures, 2 errors: missing routes `302/200 != 404`, no `user_routes` module, no CSRF form, nav link shown). GREEN: 608 tests incl. PostgreSQL; the characterization table now covers `USERS` | Range `5959d0a..bcc196d`: **medium**, `slice_budget_reached` (645 lines, about 390 tests); consent granted; reliability review `review-4d7fb0161b6b83fc` **approved** and acknowledged; its one suggestion (an `IntegrityError` only at commit) needs no change because `users.create` and `update` flush inside the write and turn constraint violations into `Conflict` (pinned by UM-2 tests) |
| UM-3b | Done | `7961017` (reset e-mail helper in `app/utils/password_reset_mail.py`, lookup regardless of case), `84f492a` (deactivate, reactivate, send-reset actions, security log), `10208dc` (review findings), final small refactor in the next commit | RED: helper import errors; characterization `302 != 404` for the three POST endpoints; no security-log lines; reset lookup not case-insensitive; then for the findings: no operator WARNING on mail failure, `['twin@example.com'] != ['Twin@Example.com']`, refused requests spending the rate-limit budget (`302 != 500`). GREEN: 630 tests incl. PostgreSQL; `7961017` alone also passed (609) | Range `b47a490..84f492a`: **high**; consent granted; 4-lens review `review-d3867010accc1cca` **approved** and acknowledged with 4 warnings and 2 suggestions, all applied in `10208dc`; that commit (**high**) got its own 4-lens review `review-41f9d581847c6b57`, **approved** and acknowledged; its 2 suggestions applied (keyword-only `reason`, ordered exact lookup) |
| UM-4 | Done | `163dd6a` (profile blueprint, `api_tokens.list_own`/`revoke_own`, docs), `62bdde8` (credential rate limit, failure logging, sidebar entry, exact 405/429 assertions), review follow-up in the next commit | RED: 24 new tests (33 failures, 12 errors: missing `list_own`/`revoke_own`, missing routes, no security-log lines, no chip link); then the 11th credential request not refused, no `*_FAILED` lines, no sidebar entry. GREEN: 659 tests incl. PostgreSQL | Range `a1307ff..62bdde8`: **high**; consent granted; 4-lens review `review-c5750696a0223e11` **approved** and acknowledged; risk lens no findings; applied: docstrings now describe the per-account limit, one field format for every `PASSWORD_CHANGE` line, unused form buttons removed; the 405/429 assertions are valid because PR #73 is in the base |

## Findings during implementation

- `AuthFailure` lives in `app/services/errors.py`; `tests/test_api_token_service.py` pins its exact set of values. The edit surface was widened to that file with the user's approval (2026-10-04).
- Flask-Login `login_user()` returns False for an inactive user instead of raising, so the login route branches on it and answers exactly like wrong credentials; the security log records `inactive`.
- An inactive user's reset link is refused inside `User.verify_reset_token`, and `update_password_from_reset` also requires `active` in its `WHERE` clause. A user reactivated within the hour can still use an earlier reset link if their password has not changed.
- `is_active` is `self.active is True`, so an unsaved user counts as inactive.
- **Users service (UM-2):** `ValidationError` for self-changes, a wrong current password and bad fields; `Conflict` for the last active administrator and duplicates; `PermissionDenied` for policy refusals and self-service on `mcp`, without a user id or on an inactive account; `NotFound` for unknown ids. A new password equal to the current one is refused, so the security log never records a change that did not happen. Deactivation also needs the `API_TOKENS` update grant, so an `mcp` administrator can reactivate but never deactivate. Username uniqueness is exact; e-mail uniqueness ignores case, and a case-only change lower-cases a legacy address. The last-administrator check locks the active administrators' rows (`FOR UPDATE` on ids, counted in Python, since PostgreSQL refuses `FOR UPDATE` with an aggregate). A password change is audited as `{"credential_changed": true}`; writing the security log is the web adapter's job.
- **Screens (UM-3a):** `/usuarios/` (`users.list_users`), `/usuarios/nuevo` (`users.new_user`), `/usuarios/<id>/editar` (`users.edit_user`); navigation group "Administración" › "Usuarios" (clause 5.3). The global domain-error handler flashes and redirects, which loses form input, so the user screens catch `ValidationError`/`Conflict`, roll back and re-render; passwords are never refilled. The forms leave e-mail format checks to the service, because WTForms `Email()` rejects special-use domains the service accepts. Account actions belong in the list's `table__actions` cell, gated with `can('update', 'users')`.
- **Account actions (UM-3b):** POST `/usuarios/<id>/desactivar` (`users.deactivate_user`), `/reactivar` (`users.reactivate_user`), `/enviar-enlace` (`users.send_reset_link`, `10 per hour` per client, counted only for authorized requests because the limiter is the innermost decorator). Security log: `USER_DEACTIVATED`, `USER_REACTIVATED`, `PASSWORD_RESET_LINK_SENT`, `PASSWORD_RESET_LINK_FAILED` with `no_email`, `inactive`, `configuration` or `delivery`; mail failures also log the exception class names for operators. The reset request matches the address as typed first, then regardless of case, and sends nothing when two legacy rows differ only by case (`ambiguous`). The account-action partial must be imported `with context`.
- **Mi perfil (UM-4):** `GET /perfil/` (`profile.show`), `POST /perfil/email`, `/perfil/contrasena`, `/perfil/tokens/<id>/revocar`; every signed-in role; reachable from the user chip and a "Cuenta › Mi perfil" sidebar entry (the chip is hidden below 900px). `api_tokens.list_own`/`revoke_own` serve only the `web` channel, an actor with a user id and an active account; another user's token is a 404. The two credential forms share `10 per hour` per signed-in account (not per address, so changing address buys no extra guesses). Security log: `PASSWORD_CHANGE_SUCCESS/FAILED`, `EMAIL_CHANGE_SUCCESS/FAILED` (masked address), `API_TOKEN_REVOKED`; reasons `wrong_current_password`, `same_password`, `duplicate_email`, `invalid_form`.
- **Pre-existing defect found:** the global `Exception` handler turns HTTP errors without their own handler (405, 429) into 500, including an exceeded login rate limit. The user approved fixing it in a separate pull request (2026-10-05): [#73](https://github.com/constant1n0/iso9001/pull/73), merged as `a1307ff`; the UM-3b tests now assert exactly 405 and 429.
- **Moved to UM-3b:** the reset e-mail helper move and its administrator-triggered wording, the security log for credential changes, and a case-insensitive e-mail lookup in the reset request (`auth_routes.py` still uses `filter_by(email=...)`).
- **Deployment order:** the model selects `users.active` on every user load, so production must run `flask db upgrade` before the services restart on the new code (or in the same maintenance step); otherwise every authenticated request fails.

## Delivery

| Slice | Pull request | Commits | Merged as | Note |
|---|---|---|---|---|
| 1 | [#69](https://github.com/constant1n0/iso9001/pull/69) | tracker, UM-1 | `c767641` | |
| 2 | [#70](https://github.com/constant1n0/iso9001/pull/70) | UM-2 | `5959d0a` | maintainer-approved `size:exception` (921) |
| 3 | [#71](https://github.com/constant1n0/iso9001/pull/71) | UM-3a | `b47a490` | maintainer-approved `size:exception` (661) |
| 4 | [#72](https://github.com/constant1n0/iso9001/pull/72) | UM-3b | `5454725` | maintainer-approved `size:exception` (1,053) |
| — | [#73](https://github.com/constant1n0/iso9001/pull/73) | 405/429 handler fix (separate branch) | `a1307ff` | |
| 5 | Pending | UM-4, this closing update | — | Final slice |

## Production deployment (pending authorization)

`users.active` is a new column (migration `f2c7a9e4b1d6`) and the model reads it on every user load, so the deployment must run `flask db upgrade` before the services restart on the new code:

1. Back up the database (`pg_dump -Fc`) and the code.
2. Fast-forward the code (git bundle), `pip install -r requirements.txt` (no dependency changes expected), `flask db upgrade`, `flask db check`.
3. The user restarts `iso9001`, `iso9001-celery-worker`, `iso9001-celery-beat` and `iso9001-mcp`.
4. Smoke tests: login, `/usuarios/`, `/perfil/`, `/mcp` 401 without a token.

## Next step

**Feature complete** once slice 5 merges. Next: the production deployment above, after explicit authorization.
