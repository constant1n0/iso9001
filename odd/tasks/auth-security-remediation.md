# Auth Security Remediation

Repository locator: `odd/tasks/auth-security-remediation.md`

## Objective

Remediate the verified authentication bootstrap, password-reset, and access-log findings through independently reviewable units, with A2 split into two bounded slices, using explicit RED → GREEN → REFACTOR evidence and preserving existing accounts.

## Problem and why it matters

Before A1, the application permitted the first unauthenticated web registrant to become `ADMINISTRADOR`, with separate empty-table checks that created a race. A1 removed that path and is now integrated. Password-reset links still trust the request Host, reset tokens remain replayable after a password change, and Gunicorn still records the token-bearing request path; those A2/A3 findings remain pending.

Evidence is recorded in audit memory `#5378`. This document plans only the verified bootstrap and reset findings; other audit sectors remain deferred.

## Authorized decisions

| Topic | Decision and source |
|---|---|
| First administrator | Local Flask CLI only; remove public web registration and preserve existing accounts. Explicit user choice. |
| TDD | `on`; explicit user choice. Python 3.11 stdlib `unittest` is the runner. Each new implementation slice must record its own observed RED, GREEN, then REFACTOR evidence; historical full-A2 evidence does not become proof for the new slices. |
| RDD | `on` globally and parent-owned. This delegated A2a implementation does not start, acknowledge, approve, or burn a review. |
| Delivery strategy | `ask-on-risk`, resolved by the user to two A2 slices after the original cohesive candidate exceeded the review budget. |
| Chain strategy | `stacked-to-main`; integrate independent units sequentially into `main`: A1 → A2a → A2b → A3. A2b is created locally from the future A2a commit, while eventual integration targets remain sequential to `main`. |
| Local authorization | Parent read-back of this plan and full mirror `#5379` is complete. Local A2a tests, source, documentation, verification, and work-unit commits are authorized; this invocation stops before A2b or native review. |
| Remote operations | None for A2. No publication, merge, network service, deployment, or remote review action is authorized. Historical A1 remote delivery remains recorded below. |
| Review size | The `400` authored changed-line PR budget still applies to future delivery of each A2 slice. A1's historical `size:exception` does not transfer; no A2 PR exception exists. Honest local implementation and commits are authorized even if the materialized slice exceeds 400 lines; report the overage without another slicing pass or cosmetic shrinking. |

## Scope

### Included

- Replace public first-user registration with a local Flask CLI provisioning command.
- Add the isolated test harness required for deterministic auth tests.
- Build reset links from a configured canonical HTTPS origin rather than request Host data, declare the email-validation dependency, and keep reset-request responses indistinguishable for syntactically valid known/unknown addresses, invalid configuration, and mail-send failures. Malformed email retains existing form-validation behavior.
- Invalidate reset tokens after a successful password change, make reset consumption atomic with compare-and-swap behavior, and test expiry/replay behavior.
- Prevent token-bearing reset request targets from entering Gunicorn access logs.
- Keep behavior, tests, technical documentation, and rollback evidence in the same work unit.

### Excluded

- Changes to existing account records, roles, or database contents.
- Other audit sectors, general auth redesign, MFA, authorization changes, or dependency upgrades.
- PostgreSQL, Redis, SMTP, or other service contact during tests.
- All A2 remote operations; deployment, production services, publication, integration, A3 implementation, and unrelated branches/features remain excluded.
- Artificial code reduction, omitted tests, minification, or non-cohesive splitting to satisfy a line heuristic.

## Constraints and test environment

- Use Python 3.11 and stdlib `unittest`; do not introduce pytest solely for this work.
- Use an isolated in-memory SQLite database, mocked mail, test-only secrets, and non-routable example URLs.
- Do not contact a network database, Redis, SMTP, or external services.
- The ignored Python 3.11 `venv` now contains the unchanged `requirements.txt` dependencies; stdlib `unittest` remains the runner and pytest was not added.
- A1 removed import-time creation of `logs/security.log`; tests disable file logging before app initialization.
- Preserve existing Spanish UI context. New code and technical documentation default to English.
- Approximately 400 authored changed lines is an advisory implementation heuristic. The `400` authored changed-line budget governs future PR delivery unless a maintainer grants an explicit exception; it does not block honest local implementation or commits and must not cause omitted validation, compressed code, or artificial shrinking.

## Planned checks

Run for every implementation unit after dependencies are available:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
git diff --check
```

RED/GREEN/REFACTOR proof for A1 and the historical full-A2 snapshot exists. A2a, A2b, and A3 require new slice-specific evidence; planned commands and counts are not completion evidence.

## Stable tasks and review slices

### A1 — Replace public bootstrap with local CLI and add the test harness

- **Status:** COMPLETED and integrated into `main` through PR #1 at merge SHA `972f158f4ba9147b7d4dd2f12acb3cb9f5cbe518`
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

Review-size note: A1 contains `646` authored additions plus deletions before tracking-document overhead. The behavior, tests, and README remain one cohesive work unit without cosmetic shrinking or omitted coverage. The maintainer has now approved `size:exception` for the exact A1+CI PR only.

macOS environment caveat: the documented `venv/bin/flask --app run.py create-admin` command is valid when the native libraries required by the application's eager WeasyPrint imports are available to the dynamic loader. The isolated test harness exposes existing Homebrew library directories; it does not install or bypass those native requirements.

#### A1 protected delivery result

- Preparation trackers were committed as `92c2d3d852944e0c3bdb8a229e118f7b75dcf43b`; [PR #1](https://github.com/constant1n0/iso9001/pull/1) used base `07159e9` and exact head `92c2d3d`.
- The PR contained five verified commits and 12 paths with 899 additions and 180 deletions (`1,079` lines). The maintainer-approved `size:exception` was recorded in its body.
- `main` protection required strict `Python 3.11 tests` from `app_id: 15368`, enforced for administrators, disallowed force pushes/deletions, and added no reviewer-count requirement.
- [PR run 35524353813](https://github.com/constant1n0/iso9001/actions/runs/35524353813) passed on exact head `92c2d3d`; required-check readback passed and the PR was `CLEAN`/`MERGEABLE`.
- The parent merged without admin/bypass/force/delete. Readback confirmed `MERGED` at `2026-09-20T16:59:08Z`, merge SHA `972f158f4ba9147b7d4dd2f12acb3cb9f5cbe518`; both branches were preserved.
- [Main run 35524407104](https://github.com/constant1n0/iso9001/actions/runs/35524407104) passed on the exact merge SHA, and final protection readback remained unchanged.
- No deployment or live-data change occurred. A2 and A3 remain unfixed, pending, and outside this integration.

### A2 — Enforce canonical reset origins and single-use tokens

The previously completed full-A2 candidate remains an immutable historical snapshot at `fix/auth-reset@0491c02724a612065eb39768e64377a085e108f5`. Native review approved that exact snapshot, and its receipt was acknowledged and burned. The snapshot is a decomposition reference only: its approval, receipt, and review state do **not** transfer to either new slice.

The parent read back this repository document and full Engram mirror `#5379`. Local A2a source, tests, documentation, verification, and work-unit commit closure are now authorized. Publication, merge, remote review action, network service, A2b, and A3 work remain outside this invocation.

#### A2a — Enforce canonical HTTPS reset origin and generic requests

- **Status:** implemented and verified locally; parent-owned native assessment/consent remains pending
- **Route:** `DIRECT` through bounded parent delegation — trigger evidence is the multi-file behavior, dependency, five-test, and documentation work unit plus the user's explicit local-preparation authorization. One writer; no child delegation is allowed.
- **Branch:** `fix/auth-reset-origin`, created from exact local `main@9366058123bf60705804a70715dea63e763abde1`
- **Forecast:** approximately 370–390 authored additions plus deletions, including tracker changes, with uncertainty of ±30 until materialized.
- **Review budget:** `400` for future PR delivery; no `size:exception`. Local implementation and commits may materialize the honest size, and tests/docs/source must not be shrunk to meet it.
- **Behavior commit:** `8a7fc4169f48a4976352fc6a1bda639ddb1fff20`
- **Observed behavior-commit size:** 377 additions and 53 deletions (`430` authored changed lines), including the prepared tracker.
- **Observed final slice size:** `478` authored changed lines versus `main@9366058123bf60705804a70715dea63e763abde1`, including this tracking closure.

Acceptance and verification checklist:

- [x] Reset links use an explicitly configured canonical HTTPS origin and never request Host data.
- [x] Configuration rejects a missing, malformed, non-HTTPS, or credential-bearing canonical origin without silently trusting the request.
- [x] A malicious Host header cannot alter the emailed reset origin.
- [x] Syntactically valid known and unknown email requests, invalid reset-origin configuration, and mail-send failures return indistinguishable generic outcomes without exposing account existence or operational failures.
- [x] Malformed email retains the existing form-validation behavior; A2a does not broaden generic-response handling to invalid form input.
- [x] The email-validation dependency is declared explicitly rather than relying on a transitive installation.
- [x] Exactly five focused A2a tests in the shared `tests/test_auth_reset.py` scaffold cover canonical-origin validation, Host isolation, generic request/failure behavior, and secret-safe logs without external services.
- [x] Deployment documentation identifies the required canonical HTTPS origin and email-validation dependency.
- [x] Observed RED, GREEN, and REFACTOR evidence, the focused command, full-suite command, exact results, actual authored size, and `git diff --check` result are recorded after implementation.

Planned focused command: `venv/bin/python -m unittest discover -s tests -p 'test_auth_reset.py' -v` (five A2a tests now; six A2b tests will be added to the same shared scaffold later). The full-suite command remains the repository command above.

Rollback boundary: revert only A2a canonical-origin configuration, reset-request/email behavior, declared dependency, five focused tests, deployment documentation, and this slice's tracking updates. Token invalidation and A2b behavior must remain untouched.

##### A2a implementation evidence

The five original A2a tests were written first against the base implementation. Focused RED:

```text
Ran 5 tests in 1.148s
FAILED (failures=4, errors=13)
```

The failures showed request Host control, email delivery with missing/HTTP configuration, distinguishable failure responses, and missing canonical-origin validation symbols. One secret-safe logging test already passed; no dependency failure was invented because the existing venv already contained `email-validator`.

GREEN before refactor assessment:

```text
Ran 5 tests in 0.692s
OK
```

REFACTOR assessment found no unnecessary structural change to make: the slice already matched the approved historical A2a boundary, kept the old reset-password route and model behavior, and delayed token-only helpers/imports. Final focused verification:

```text
Ran 5 tests in 0.677s
OK
```

Full isolated Flask/SQLite suite:

```text
Ran 15 tests in 1.348s
OK
```

`venv/bin/python -m pip check` reported `No broken requirements found.` Both `git diff --check` and `git diff --cached --check` passed with no output. The tests mocked mail and used non-routable example origins, the test-only secret, and in-memory SQLite; no DNS, SMTP, PostgreSQL, Redis, network service, or production secret was used. SQLite integration coverage does not claim PostgreSQL equivalence.

Changed paths:

- `README.md`
- `app/config.py`
- `app/routes/auth_routes.py`
- `odd/tasks/auth-security-remediation.md`
- `requirements.txt`
- `tests/test_auth_reset.py`

Security boundary: canonical-origin parsing rejects unsafe URL components and request Host data; syntactically valid account/failure cases share the same redirect and generic flash. Existing malformed-email form validation remains unchanged. Reset-token payload, verification, replay, and atomic update behavior remain deferred to A2b. Review state is pending parent-owned native assessment/consent; this agent did not start or approve review.

#### A2b — Make reset tokens single-use with atomic consumption

- **Status:** planned; branch and implementation do not exist yet
- **Route:** `DIRECT` through bounded parent delegation — trigger evidence is the multi-file token semantics, atomic persistence, six-test, and documentation work unit plus the user's explicit local-preparation authorization. One writer; no child delegation is allowed.
- **Branch:** `fix/auth-reset-tokens`, to be created locally from the future verified A2a commit; intended integration remains sequential to `main` after A2a.
- **Forecast:** approximately 390–410 authored additions plus deletions, including tracker changes, with uncertainty of ±30 until materialized.
- **Review budget:** `400`; no `size:exception`. If the cohesive actual size exceeds the budget, stop and report it rather than compressing or omitting work.
- **Commit:** pending.

Acceptance and verification checklist:

- [ ] A reset token is valid before use, is subject to expiry, and is bound to the account's current password state without a schema migration or new token library.
- [ ] A successful reset changes the password and invalidates the consumed token.
- [ ] Token consumption uses an atomic compare-and-swap update so concurrent or stale consumers cannot both succeed.
- [ ] Replayed and losing concurrent tokens fail through the same generic invalid-token path without exposing sensitive details.
- [ ] Malformed, expired, and unknown-user tokens fail safely without changing account data.
- [ ] Exactly six focused A2b tests are added later to the shared `tests/test_auth_reset.py` scaffold to cover validity, expiry, malformed/unknown tokens, replay, and atomic stale-consumer behavior using isolated SQLite and no external services.
- [ ] Technical documentation explains single-use token semantics, the atomicity boundary, and operational rollback implications.
- [ ] Observed RED, GREEN, and REFACTOR evidence, the focused command, full-suite command, exact results, actual authored size, and `git diff --check` result are recorded after implementation.

Planned focused command: `venv/bin/python -m unittest discover -s tests -p 'test_auth_reset.py' -v` (the shared module will contain the five A2a tests plus six A2b tests after A2b). The full-suite command remains the repository command above.

Rollback boundary: revert only A2b token fingerprint/validation behavior, atomic compare-and-swap reset update, six focused tests, technical documentation, and this slice's tracking updates. A2a canonical-origin and generic request protections must remain intact.

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
main @ 9366058 (current exact local base; A1+CI already integrated)
  └─ A2a fix/auth-reset-origin
       └─ A2b fix/auth-reset-tokens (rooted locally from future A2a commit)
            └─ A3 fix/auth-log-redaction (out of current scope)
```

- Strategy: `stacked-to-main`.
- Integration order remains sequential to `main`: A1, then A2a, then A2b, then A3. A1 is integrated; A2a/A2b publication and integration remain unauthorized, and A3 is out of current scope.
- Initial authored running line count: `646` for A1 code, tests, and README.
- Behavior commit: `213ef59f4400d178bb06ba74fe06cbd42d5e70dc`, with `697` additions and `180` deletions overall.
- Behavior commit tracking overhead: `231` additions for this task document, separate from the `466` additions and `180` deletions (`646` authored lines) in A1 code, tests, and README.
- Tracking-only closure commit: separate from behavior; its immutable SHA is reported externally to avoid self-referential hashing.
- Tracking-only closure delta: `11` additions and `7` deletions in this document.
- Original full forecast: approximately `590–820` authored additions plus deletions.
- Revised A2 slice forecasts: A2a `370–390 ±30` and A2b `390–410 ±30`, each including tracker changes and uncertain until materialized.
- Work-unit approximately 400-line heuristic: advisory only; PR budget `400` still applies.
- Size handling: the original full-A2 snapshot was split once into cohesive A2a/A2b review units. The maintainer approved `size:exception` for the exact A1+CI PR only; no exception or review approval transfers to A2a/A2b. Do not shrink content artificially or omit tests/docs.
- Historical full-A2 snapshot: `fix/auth-reset@0491c02724a612065eb39768e64377a085e108f5`, natively approved with its receipt acknowledged/burned. It is preserved untouched and supplies no review inheritance.
- A2a behavior commit: `8a7fc4169f48a4976352fc6a1bda639ddb1fff20` (`430` authored changed lines including prepared tracking). Final A2a slice: `478` authored changed lines versus `main@9366058123bf60705804a70715dea63e763abde1`, including the tracking-only closure.
- A1 behavior `213ef59f4400d178bb06ba74fe06cbd42d5e70dc`, tracking closure `636649426e87cc1b9519907ffab8de2c7646f833`, CI `9f5723c1c431d2f15b3c676fc4058f1dc22cad89`, publication proof `d76821939046ef8aa68e8a23fd69e5c78e76bac3`, and integration preparation `92c2d3d852944e0c3bdb8a229e118f7b75dcf43b` were integrated by PR #1 as merge `972f158f4ba9147b7d4dd2f12acb3cb9f5cbe518`.
- Existing untracked `.atl/` and `.codegraph/` directories must remain untouched.

## Known limitations and blockers

- The ignored Python 3.11 virtual environment and existing requirements are installed locally; no global packages or dependency files changed.
- No pre-existing automated tests, test runner configuration, or management CLI existed before A1.
- The canonical production origin value is an environment/deployment input; tests must use an isolated non-routable value.
- `PasswordResetRequestForm` uses WTForms `Email`, while `email-validator` is not declared in `requirements.txt`; A2a must make the existing environment dependency explicit. The current venv already contains it from the historical full-A2 work, so no dependency installation or invented missing-dependency RED is needed.
- A1 used only Flask's isolated test client/CLI and in-memory SQLite. No live database, Redis, SMTP, external service, or deployment environment was contacted.

## Progress and next step

- A1: COMPLETED and integrated through PR #1 at `main@972f158f4ba9147b7d4dd2f12acb3cb9f5cbe518`
- A2a: implemented and verified locally at behavior commit `8a7fc4169f48a4976352fc6a1bda639ddb1fff20`; tracking-only closure and parent-owned native assessment/consent remain
- A2b: planned from the future verified A2a commit; branch not created
- A3: pending
- Parent read-back gate: completed for this file and full mirror `#5379`; the parent corrected A2a boundaries before authorizing implementation.
- **Next step:** commit this tracking-only closure, mirror and read back the full document, then return for parent-owned native assessment/consent. No publication, merge, remote review action, deployment, production service, A2b, or A3 work is authorized.
