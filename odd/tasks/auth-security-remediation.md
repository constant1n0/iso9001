# Auth Security Remediation

Repository locator: `odd/tasks/auth-security-remediation.md`

## Objective

Remediate the verified authentication bootstrap and password-reset findings in three independently reviewable units, using explicit RED → GREEN → REFACTOR evidence and preserving existing accounts.

## Problem and why it matters

The application currently permits the first unauthenticated web registrant to become `ADMINISTRADOR`, with separate empty-table checks that create a race. Password-reset links trust the request Host, reset tokens remain replayable after a password change, and Gunicorn records the token-bearing request path. Together, these behaviors permit administrative takeover during bootstrap and weaken reset-token confidentiality and lifecycle control.

Evidence is recorded in audit memory `#5378`. This document plans only the verified bootstrap and reset findings; other audit sectors remain deferred.

## Authorized decisions

| Topic | Decision and source |
|---|---|
| First administrator | Local Flask CLI only; remove public web registration and preserve existing accounts. Explicit user choice. |
| TDD | `on`; explicit user choice. Each task must record observed RED, GREEN, then REFACTOR evidence. |
| RDD | `off`; native/default status handling remains in effect. |
| Delivery strategy | `ask-on-risk`, resolved by the user to chained units. |
| Chain strategy | `stacked-to-main`; integrate independent units into `main` in order: A1 → A2 → A3. |
| Remote operations | The user explicitly authorized publication of the current `fix/auth-bootstrap` branch only. A1/CI commits were pushed there and CI evidence was read with the configured GitHub CLI session; no PR, merge, deployment, settings mutation, other branch, or broader remote permission was authorized. |

## Scope

### Included

- Replace public first-user registration with a local Flask CLI provisioning command.
- Add the isolated test harness required for deterministic auth tests.
- Build reset links from a configured canonical origin rather than request Host data.
- Invalidate reset tokens after a successful password change and test expiry/replay behavior.
- Prevent token-bearing reset request targets from entering Gunicorn access logs.
- Keep behavior, tests, technical documentation, and rollback evidence in the same work unit.

### Excluded

- Changes to existing account records, roles, or database contents.
- Other audit sectors, general auth redesign, MFA, authorization changes, or dependency upgrades.
- PostgreSQL, Redis, SMTP, or other service contact during tests.
- Remote operations beyond the explicitly authorized current-branch publication and read-only CI evidence; pull requests, merges, deployment, and production configuration mutation remain excluded.
- Artificial code reduction, omitted tests, minification, or non-cohesive splitting to satisfy a line heuristic.

## Constraints and test environment

- Use Python 3.11 and stdlib `unittest`; do not introduce pytest solely for this work.
- Use an isolated in-memory SQLite database, mocked mail, test-only secrets, and non-routable example URLs.
- Do not contact a network database, Redis, SMTP, or external services.
- The ignored Python 3.11 `venv` now contains the unchanged `requirements.txt` dependencies; stdlib `unittest` remains the runner and pytest was not added.
- A1 removed import-time creation of `logs/security.log`; tests disable file logging before app initialization.
- Preserve existing Spanish UI context. New code and technical documentation default to English.
- The approximately 400 changed-line threshold is advisory for each cohesive task. It must not cause omitted validation, compressed code, or artificial task splits.

## Planned checks

Run for every implementation unit after dependencies are available:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
git diff --check
```

RED/GREEN/REFACTOR proof for A1 is recorded below. A2 and A3 evidence remains pending.

## Stable tasks and review slices

### A1 — Replace public bootstrap with local CLI and add the test harness

- **Status:** COMPLETED, independently/parent verified, and published to `origin/fix/auth-bootstrap`
- **Route:** `delegated` — multi-file implementation and preparation triggers apply.
- **Branch:** `fix/auth-bootstrap`
- **Base boundary:** `main` at `07159e95de7686f5c2f0cedb0f63af332687c93e`
- **Forecast:** approximately 330–430 authored additions plus deletions.
- **Observed size:** 646 authored additions plus deletions for A1 code, tests, and README; the cohesive unit exceeded the advisory forecast and was not artificially split.
- **Behavior commit:** `213ef59f4400d178bb06ba74fe06cbd42d5e70dc`

Acceptance criteria:

- [x] `/register` cannot create an account, including when the users table is empty.
- [x] Empty installations no longer redirect requests to public registration.
- [x] A local Flask CLI command creates one `ADMINISTRADOR` with a hashed password.
- [x] The CLI refuses provisioning when any account already exists and leaves no partial record.
- [x] Existing accounts and database contents remain unchanged.
- [x] Tests use isolated SQLite, create no security log artifact, and perform no network or service calls.
- [x] Operator-facing bootstrap instructions describe the CLI workflow.
- [x] Observed RED, GREEN, and REFACTOR evidence plus exact check results are recorded.

Rollback boundary: revert only A1 behavior, tests, and documentation. No schema rollback is expected; an administrator created by the CLI remains an existing account. Restoring public registration requires an explicit security decision.

#### A1 implementation evidence

Environment setup completed with the existing dependency lock surface:

```bash
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

The requirements installation succeeded without changing `requirements.txt` or upgrading global tooling. The first test attempt stopped during collection because eagerly imported WeasyPrint could not locate `libgobject-2.0-0`. A direct probe then required `libfontconfig-1`. Existing Homebrew native libraries were already present; the isolated test bootstrap now exposes their existing library directories on macOS before importing the app. No native package was installed and no application architecture was changed to bypass WeasyPrint.

Meaningful RED after resolving that environment prerequisite:

```text
Ran 10 tests in 0.421s
FAILED (failures=6, errors=2)
```

Observed failures proved that `/register` returned 200, `create-admin` did not exist, the factory rejected test overrides, and importing auth created the security log. Two baseline tests passed.

GREEN before normalization:

```text
Ran 10 tests in 0.696s
OK
```

REFACTOR assessment removed the now-dead registration logger, expanded boundary validation coverage, and cleaned up the managed test log handler. Verification after refactor:

```text
Ran 10 tests in 0.704s
OK
```

Focused CLI help smoke test through Flask's test runner:

```text
Ran 1 test in 0.023s
OK
```

`git diff --check` passed with no output. Flask-Caching emits one upstream deprecation warning during app creation; it does not fail the suite and is outside A1.

Closure verification:

- Independent functional/security verification: **PASS** after reading all candidate paths, running 10/10 tests, discovering the real Flask CLI under isolated configuration, and finding no candidate-caused blocker.
- Parent verification: 10/10 tests passed in `0.752s`; `git diff --check` was clean; tracked diff, status, and log were inspected.
- Closure verification after the documentation-only update: 10/10 tests passed in `0.732s`; `git diff --check` was clean.
- Native risk assessment command `gentle-ai review assess --cwd /Users/dcm/work/iso9001 --json` was unavailable because it refused undeclared untracked inventory. The assessment is recorded as unavailable and conservatively high; the independent verifier result remains PASS. It was not retried.
- RDD remained off. No RDD invocation, receipt, consent, review START, or review status exists.
- Linux CI verification is recorded in [`odd/tasks/github-actions-ci.md`](github-actions-ci.md): [run 35522042241](https://github.com/constant1n0/iso9001/actions/runs/35522042241) passed 10/10 tests on Python 3.11.16 and Ubuntu 24.04 for head `9f5723c1c431d2f15b3c676fc4058f1dc22cad89`.

Changed paths:

- `app/commands.py`
- `app/__init__.py`
- `app/config.py`
- `app/forms.py`
- `app/routes/auth_routes.py`
- `app/templates/register.html` (removed)
- `app/utils/security_logger.py`
- `tests/test_auth_bootstrap.py`
- `README.md`

Concurrency guarantee: the public race is removed. The privileged local command checks for accounts before prompting and again before writing, but it does not claim cross-process serialization. Simultaneous local invocations remain an operator constraint; no migration or locking table was introduced.

Review-size note: A1 contains `646` authored additions plus deletions before tracking-document overhead. No PR is being created, and no `size:exception` approval exists. The behavior, tests, and README remain one cohesive local work unit without cosmetic shrinking or omitted coverage. Any later PR preparation must resolve the greater-than-400-line budget under ordinary policy.

macOS environment caveat: the documented `venv/bin/flask --app run.py create-admin` command is valid when the native libraries required by the application's eager WeasyPrint imports are available to the dynamic loader. The isolated test harness exposes existing Homebrew library directories; it does not install or bypass those native requirements.

### A2 — Enforce canonical-origin reset links and token invalidation

- **Status:** pending
- **Route:** `delegated` — multi-file implementation and preparation triggers apply.
- **Branch:** `fix/auth-reset`, planned from the previous local A1 unit; integration or merge remains unauthorized.
- **Forecast:** approximately 190–280 authored additions plus deletions.
- **Commit:** pending

Acceptance criteria:

- [ ] Reset links use an explicitly configured canonical origin and never request Host data.
- [ ] Tests prove a malicious Host header cannot alter the emailed reset origin.
- [ ] A reset token is valid before use, subject to expiry, and invalid after the password changes.
- [ ] Malformed, expired, unknown-user, and replayed tokens fail without exposing sensitive details.
- [ ] Mail is mocked; tests use no real secret, routable URL, SMTP connection, or external service.
- [ ] Deployment documentation identifies the required canonical-origin configuration.
- [ ] Observed RED, GREEN, and REFACTOR evidence plus exact check results are recorded.

Rollback boundary: revert only A2 model/configuration/route behavior, tests, and documentation. Prefer a standard-library fingerprint approach that requires no schema migration or new library.

### A3 — Remove reset tokens from access logging

- **Status:** pending
- **Route:** `delegated` — configuration, test, and documentation changes form one review unit.
- **Branch:** `fix/auth-log-redaction`, planned from the previous local A2 unit; integration or merge remains unauthorized.
- **Forecast:** approximately 70–110 authored additions plus deletions.
- **Commit:** pending

Acceptance criteria:

- [ ] Gunicorn access logs do not contain request paths, query strings, or reset tokens.
- [ ] The access log retains useful non-secret request metadata such as client, time, status, response size, user agent, and duration.
- [ ] A configuration-level regression test proves token-bearing request targets are excluded.
- [ ] Documentation records the observability tradeoff and safe format.
- [ ] Observed RED, GREEN, and REFACTOR evidence plus exact check results are recorded.

Rollback boundary: restore only the previous Gunicorn logging configuration, its test, and documentation; doing so knowingly restores token-path disclosure.

## Delivery ledger

```text
main @ 07159e9
  └─ A1 fix/auth-bootstrap @ 213ef59
       └─ A2 fix/auth-reset
            └─ A3 fix/auth-log-redaction
```

- Strategy: `stacked-to-main`.
- Planned integration order: A1, then A2, then A3. Branches are based on the previous local unit; no merge or integration is authorized yet.
- Initial authored running line count: `646` for A1 code, tests, and README.
- Behavior commit: `213ef59f4400d178bb06ba74fe06cbd42d5e70dc`, with `697` additions and `180` deletions overall.
- Behavior commit tracking overhead: `231` additions for this task document, separate from the `466` additions and `180` deletions (`646` authored lines) in A1 code, tests, and README.
- Tracking-only closure commit: separate from behavior; its immutable SHA is reported externally to avoid self-referential hashing.
- Tracking-only closure delta: `11` additions and `7` deletions in this document.
- Original full forecast: approximately `590–820` authored additions plus deletions.
- Revised full forecast after observed A1 size: approximately `906–1,036` authored additions plus deletions if A2 and A3 remain within their current forecasts.
- Per-task approximately 400-line heuristic: advisory only.
- Size handling: one honest cohesive slicing pass has produced A1/A2/A3. A1 exceeds 400 lines, no PR is being created, and no `size:exception` is approved. Later PR preparation must resolve the budget under ordinary policy; do not shrink content artificially.
- A1 behavior `213ef59f4400d178bb06ba74fe06cbd42d5e70dc`, tracking closure `636649426e87cc1b9519907ffab8de2c7646f833`, and later CI `9f5723c1c431d2f15b3c676fc4058f1dc22cad89` are published to `origin/fix/auth-bootstrap`. No pull request, merge, or deployment exists.
- Existing untracked `.atl/` and `.codegraph/` directories must remain untouched.

## Known limitations and blockers

- The ignored Python 3.11 virtual environment and existing requirements are installed locally; no global packages or dependency files changed.
- No pre-existing automated tests, test runner configuration, or management CLI existed before A1.
- The canonical production origin value is an environment/deployment input; tests must use an isolated non-routable value.
- `PasswordResetRequestForm` uses WTForms `Email`, while `email-validator` is not declared in `requirements.txt`; verify this during A2 RED without expanding A1.
- A1 used only Flask's isolated test client/CLI and in-memory SQLite. No live database, Redis, SMTP, external service, or deployment environment was contacted.

## Progress and next step

- A1: COMPLETED and published on `origin/fix/auth-bootstrap` at behavior commit `213ef59f4400d178bb06ba74fe06cbd42d5e70dc`; tracking closure `636649426e87cc1b9519907ffab8de2c7646f833` and later CI `9f5723c1c431d2f15b3c676fc4058f1dc22cad89` are on the same branch
- A2: pending
- A3: pending
- Parent read-back gate: verified by the parent for this file and full Engram mirror `#5379`.
- **Next step:** A2 remains pending for a subsequent local unit based on A1. No PR, merge, deployment, repository-setting change, other-branch publication, or A2/A3 implementation is authorized by this closure.
