# Auth Security Remediation

Repository locator: `odd/tasks/auth-security-remediation.md`

## Objective

Remediate the verified authentication bootstrap, password-reset, and access-log findings through independently reviewable units, with A2 split into two bounded slices, using explicit RED → GREEN → REFACTOR evidence and preserving existing accounts.

## Problem and why it matters

Before A1, the application permitted the first unauthenticated web registrant to become `ADMINISTRADOR`, with separate empty-table checks that created a race. A1 removed that path and is now integrated. A2a and A2b are closed locally on their preserved stacked branches but are not published or integrated. The current A3 branch inherits those local protections and now omits the token-bearing request target and referrer from Gunicorn access logging; local commit closure is the remaining authorized step.

Evidence is recorded in audit memory `#5378`. This document plans only the verified bootstrap and reset findings; other audit sectors remain deferred.

## Authorized decisions

| Topic | Decision and source |
|---|---|
| First administrator | Local Flask CLI only; remove public web registration and preserve existing accounts. Explicit user choice. |
| TDD | `on`; explicit user choice. Python 3.11 stdlib `unittest` is the runner. Each new implementation slice must record its own observed RED, GREEN, then REFACTOR evidence; historical full-A2 evidence does not become proof for the new slices. |
| RDD | Clone-local mode is `off` by explicit user choice (`off/clone_local`; the global default remains on). Review consent and control are user-owned. The DIRECT writer must not run native review status, assessment, start, actor, acknowledgement, or reactivation actions for A3. The parent alone may run a read-only risk classification to size ordinary functional verification; that classification does not start native review or enable RDD. Historical A2a approval remains historical only. |
| Delivery strategy | `ask-on-risk` selected the two A2 slices and remains the A3 default. After A2b honestly materialized above budget, the user explicitly selected `size:exception`; A2b is `exception-ok` for its completed local closure only. The exception does not transfer to A2a, A3, publication, or review consent. |
| Chain strategy | `stacked-to-main`; integrate independent units sequentially into `main`: A1 → A2a → A2b → A3. A2b was created locally from exact A2a tip `668e74abfe5db1d2672d088c6373d7c3d3867bef`; A3 is created from exact A2b tracking tip `cce12c852327f732edd53fb69a8a23fb063cf885`. Eventual integration targets remain sequential to `main`. |
| Local authorization | A2a is closed locally at `668e74abfe5db1d2672d088c6373d7c3d3867bef`. A2b behavior `fe3773e98eee80c15f89410dc94e8e87afd09740` and tracking closure `cce12c852327f732edd53fb69a8a23fb063cf885` are closed locally without native approval. The user explicitly authorized bounded A3 local implementation, tests, and commits without RDD, publication, or merging. |
| Remote operations | None for A2 or A3. No publication, push, PR, merge, network service, deployment, remote review, or other remote application operation is authorized. Historical A1 remote delivery remains recorded below. |
| Review size | The `400` authored changed-line budget remains the default. The user approved `size:exception` for this exact cohesive A2b candidate after its 483-line pre-closure measurement (decision memory `#5511`, topic `delivery/auth-reset-a2b-size-exception`). Necessary tracking may increase the final count. No exception transfers to A2a or A3. |

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
- All A2/A3 remote operations; deployment, production services, publication, integration, unrelated branches/features, and unrelated authentication fixes remain excluded.
- Artificial code reduction, omitted tests, minification, or non-cohesive splitting to satisfy a line heuristic.

## Constraints and test environment

- Use Python 3.11 and stdlib `unittest`; do not introduce pytest solely for this work.
- Use an isolated in-memory SQLite database, mocked mail, test-only secrets, and non-routable example URLs.
- Do not contact a network database, Redis, SMTP, or external services.
- The ignored Python 3.11 `venv` now contains the unchanged `requirements.txt` dependencies; stdlib `unittest` remains the runner and pytest was not added.
- A1 removed import-time creation of `logs/security.log`; tests disable file logging before app initialization.
- Preserve existing Spanish UI context. New code and technical documentation default to English.
- Approximately 400 authored changed lines is an advisory implementation heuristic. The `400` authored changed-line budget governs future PR delivery unless a maintainer grants an explicit exception; it does not block honest local implementation and must not cause omitted validation, compressed code, or artificial shrinking.

## Planned checks

Run for every implementation unit after dependencies are available:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
git diff --check
```

RED/GREEN/REFACTOR proof for A1, A2a, A2b, A3, and the historical full-A2 snapshot is recorded in their respective sections.

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

The prior parent readback found that Engram observation `#5379` held only a summary rather than the full repository document. That mirror gap is tracking-only and is repaired during A3 preparation without changing the valid repository history. A2a and A2b are closed locally. The user skipped the native A2b blocker and explicitly disabled clone-local RDD; this is not native approval. Publication, merge, remote review action, and network service remain outside scope.

#### A2a — Enforce canonical HTTPS reset origin and generic requests

- **Status:** implemented, verified, and natively approved locally; acknowledgement consumed and lineage closed
- **Route:** `DIRECT` through bounded parent delegation — trigger evidence is the multi-file behavior, dependency, five-test, and documentation work unit plus the user's explicit local-preparation authorization. One writer; no child delegation is allowed.
- **Branch:** `fix/auth-reset-origin`, created from exact local `main@9366058123bf60705804a70715dea63e763abde1`
- **Forecast:** approximately 370–390 authored additions plus deletions, including tracker changes, with uncertainty of ±30 until materialized.
- **Review budget:** `400` for future PR delivery; no `size:exception`. Local implementation and commits may materialize the honest size, and tests/docs/source must not be shrunk to meet it.
- **Behavior commit:** `8a7fc4169f48a4976352fc6a1bda639ddb1fff20`
- **Tracking closure / exact A2a tip:** `668e74abfe5db1d2672d088c6373d7c3d3867bef`
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

Security boundary: canonical-origin parsing rejects unsafe URL components and request Host data; syntactically valid account/failure cases share the same redirect and generic flash. Existing malformed-email form validation remains unchanged. Reset-token payload, verification, replay, and atomic update behavior remain deferred to A2b.

A2a native review approved the exact candidate and its acknowledgement consumed lineage `review-83dd8f35c54653b4`, target `sha256:4b87516994d51d02efa4f983cd4baeca3fe1d2facb7581bf2145967d2e3e9908`, at consumed revision `sha256:f93b2e209af4f743a48c0186cdd1b1bae9f196d30f0a5382ba62ac209d290bbf` (memory `#5507`). That review is not reusable for A2b.

#### A2b — Make reset tokens single-use with atomic consumption

- **Status:** functionally implemented, independently verified, and closed locally under the explicit A2b size exception; no native approval was requested or granted
- **Route:** `delegated` — multi-file token semantics, atomic persistence, six focused tests, technical documentation, and this preparation update form one bounded work unit. One direct writer; no child delegation or SDD/review actor is allowed.
- **Branch:** `fix/auth-reset-tokens`, created locally from exact A2a tip `668e74abfe5db1d2672d088c6373d7c3d3867bef`; intended integration remains sequential to `main` after A2a.
- **Base boundary:** `fix/auth-reset-origin@668e74abfe5db1d2672d088c6373d7c3d3867bef`.
- **Forecast:** approximately 360–410 authored additions plus deletions including preparation and closure tracking. The preserved historical implementation contributed a 309-line non-tracker decomposition reference, not reusable evidence.
- **Observed size before commit-closure tracking:** 429 additions and 54 deletions (`483` authored lines): `README.md` 16/0, `app/models.py` 120/14, `app/routes/auth_routes.py` 20/7, `odd/tasks/auth-security-remediation.md` 97/32, and `tests/test_auth_reset.py` 176/1.
- **Review budget:** `400`; explicit `size:exception` approved for this exact cohesive A2b work unit. Final actual size must be reported honestly without shrinking or transferring the exception.
- **Behavior commit:** `fe3773e98eee80c15f89410dc94e8e87afd09740` (`436` additions and `57` deletions; `493` authored lines versus the exact A2a base after exception tracking).
- **Tracking closure / exact A3 base:** `cce12c852327f732edd53fb69a8a23fb063cf885`.

Acceptance and verification checklist:

- [x] A reset token is valid before use, is subject to expiry, and is bound to the account's current password state without a schema migration or new token library.
- [x] A successful reset changes the password and invalidates the consumed token.
- [x] Token consumption uses an atomic compare-and-swap update so concurrent or stale consumers cannot both succeed.
- [x] Replayed and losing concurrent tokens fail through the same generic invalid-token path without exposing sensitive details.
- [x] Malformed, expired, legacy, and unknown-user tokens fail safely without changing account data.
- [x] Exactly six focused A2b tests were added to the shared `tests/test_auth_reset.py` scaffold while preserving all five A2a tests; they cover validity/privacy, expiry and malformed/legacy/unknown payloads, replay, independent password changes, stale compare-and-swap consumption, and database failure using isolated SQLite without external services.
- [x] Technical documentation explains single-use token semantics, the atomicity boundary, legacy-link behavior, session limitation, test limitation, and operational rollback implications.
- [x] Observed RED, GREEN, and REFACTOR evidence, focused and full-suite results, dependency health, actual authored size, and `git diff --check` are recorded.

Focused command: `venv/bin/python -m unittest discover -s tests -p 'test_auth_reset.py' -v` (the shared module contains the five A2a tests plus six A2b tests). The full-suite command remains the repository command above.

Rollback boundary: revert only A2b token fingerprint/validation behavior, atomic compare-and-swap reset update, six focused tests, technical documentation, and this slice's tracking updates. A2a canonical-origin and generic request protections must remain intact.

##### A2b preparation record

- **TDD source:** explicit user choice and this task contract; stdlib `unittest` on Python 3.11. Add exactly six A2b tests to `tests/test_auth_reset.py` while preserving all five A2a tests, observe the new tests fail for the intended behavior, implement the minimum production change, then run GREEN and a REFACTOR assessment.
- **Implementation boundary:** bind each signed timed token to a secret-keyed fingerprint of the stored password hash; strictly reject malformed, expired, legacy, or unknown-user payloads; carry the verified password snapshot into an SQLAlchemy compare-and-swap update; and route zero-row or database-failure consumption through the existing generic invalid-token path. Do not add a schema migration, dependency, token library, service call, or session-revocation claim.
- **Target paths:** `app/models.py`, `app/routes/auth_routes.py`, `tests/test_auth_reset.py`, `README.md`, and this tracker only. Existing A2a configuration, generic reset-request behavior, five tests, and Spanish UI copy remain protected.
- **Six-test coverage plan:** current-state payload/privacy and validity; expiry plus malformed/legacy/unknown-user rejection; successful reset and replay rejection; independent password change invalidation; stale-consumer compare-and-swap loss; and database-failure rollback with a non-sensitive generic response.
- **Checks:** focused runner above; `venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`; `venv/bin/python -m pip check`; `git diff --check`; tracked-status and authored-line review against the exact A2a base. The Flask test client and in-memory SQLite exercise the runtime boundary; mail remains mocked and no external service is contacted.
- **Delivery controls at preparation time:** `ask-on-risk` and `stacked-to-main` remained unchanged, and no `size:exception` was then authorized. The honest slice later exceeded 400 lines, stopped for parent review without shrinking, and received the explicit bounded exception recorded above.

##### A2b implementation evidence

The six A2b regression tests were added first while production remained unchanged. Focused RED:

```text
Ran 11 tests in 2.268s
FAILED (failures=8, errors=2)
```

All five A2a tests passed. The six new methods exposed missing compare-and-swap support, an uncaught malformed payload, accepted legacy and malformed-fingerprint payloads, token survival after password changes, successful replay, raw payload shape without a password-state fingerprint, and a database failure returning HTTP 500.

After the minimum model and route implementation, focused GREEN:

```text
Ran 11 tests in 1.982s
OK
```

REFACTOR assessment found no production restructuring to justify. The invalid-token test was strengthened to drive malformed, legacy, unknown-user, and expired tokens through the same route response and verify that the stored password remains unchanged. Final focused verification:

```text
Ran 11 tests in 1.985s
OK
```

Full isolated Flask/SQLite suite:

```text
Ran 21 tests in 2.568s
OK
```

`venv/bin/python -m pip check` reported `No broken requirements found.` `git diff --check` passed with no output. The exact final per-path authored additions and deletions are recorded in the observed-size line above. The test harness used a test-only secret, non-routable example URL, in-memory SQLite, and mocked/suppressed mail; it contacted no DNS, SMTP, PostgreSQL, Redis, network service, or production secret.

Changed paths:

- `app/models.py`
- `app/routes/auth_routes.py`
- `tests/test_auth_reset.py`
- `README.md`
- `odd/tasks/auth-security-remediation.md`

Security and atomicity boundary: the signed timed payload carries only the user ID and a secret-keyed HMAC fingerprint of the stored password hash. Verification strictly validates payload shape and values, compares fingerprints in constant time, and preserves the verified password snapshot for one conditional `UPDATE ... WHERE id = ... AND password = ...`. A zero-row result or SQLAlchemy failure rolls back and returns the same invalid-link response. This prevents a stale verified request from overwriting a newer password at that database statement boundary.

Limitations: SQLite integration exercises the conditional-update behavior but does not prove PostgreSQL concurrency behavior in production. Existing authenticated sessions are not revoked. A2b does not redact token-bearing access paths; that remains A3. Legacy links are rejected after rollout, and rolling A2b back while links remain outstanding knowingly restores replayable behavior.

Rollback boundary remains the five A2b paths listed above. Revert their A2b-only changes together; keep A2a canonical-origin and generic reset-request protections. Stop issuing reset links and allow the one-hour validity window to expire before rollback unless replay risk is explicitly accepted. No schema or dependency rollback is required.

Parent closure proof: the parent read the candidate structure, ran the full suite with 21/21 passing in `2.658s`, and confirmed `git diff --check` was clean. A prior native `current-changes` assessment was unassessable because undeclared untracked `.atl/` and `.codegraph/` inventory prevents assessment. The user later skipped that blocker and disabled clone-local RDD. No review START, candidate consent, or native approval occurred.

Local commit-closure verification after recording the explicit exception ran 21/21 tests in `2.661s`, reported `No broken requirements found.`, and passed both `git diff --check` and `git diff --cached --check` with no output. Only the five declared A2b paths were staged for the behavior commit; `.atl/` and `.codegraph/` remained untracked and untouched. Tracking closure `cce12c852327f732edd53fb69a8a23fb063cf885` preserves the final 498-line A2b boundary.

### A3 — Remove reset tokens from access logging

- **Status:** independently verified with local commit closure authorized and in progress; no native approval exists or is required
- **Route:** `delegated` — the existing Gunicorn configuration, one isolated regression module, deployment documentation, and preparation/closure tracking form one bounded work unit. The route evidence is the cross-file config/test/docs change plus this preparation record; one direct writer is used, with no child delegation, SDD, RDD, or review actor.
- **Branch:** `fix/auth-log-redaction`, created from exact A2b tracking tip `cce12c852327f732edd53fb69a8a23fb063cf885`; integration or merge remains unauthorized.
- **Base boundary:** `fix/auth-reset-tokens@cce12c852327f732edd53fb69a8a23fb063cf885`.
- **Forecast:** approximately 120–170 authored additions plus deletions including preparation and closure tracking. The implementation remained cohesive but exceeded that estimate because the prepared tracker and independent source-sensitive regression were preserved rather than compressed; the exact observed size is recorded below and remains below the advisory delivery threshold.
- **Review budget:** approximately `400` authored changed lines remains advisory for local implementation and the default for any future PR. A3 has no `size:exception`; `ask-on-risk` applies if the cohesive unit approaches the delivery budget.
- **Commit:** pending

Acceptance criteria:

- [x] Gunicorn access logs do not contain request paths, query strings, or reset tokens.
- [x] The access log retains useful non-secret request metadata such as client, time, status, response size, user agent, and duration.
- [x] The format omits both the request-line atom `%(r)s` and referrer atom `%(f)s`, as well as the direct path/query atoms `%(U)s` and `%(q)s`.
- [x] A configuration-level regression test loads the real repository format and applies Gunicorn 23.0.0's real atom formatter to token-bearing `RAW_URI`, `PATH_INFO`, `QUERY_STRING`, and `HTTP_REFERER` values without opening sockets, contacting services, or writing logs.
- [x] Documentation records the observability tradeoff, safe format, and exact boundary: automatic request-target/referrer URL fields are excluded, but retained caller-supplied User-Agent content is not sanitized and must not be treated as a general header-redaction guarantee.
- [x] Observed RED, GREEN, and REFACTOR evidence plus exact check results are recorded.

Planned format behavior: replace the combined format with `%(h)s %(t)s %(s)s %(b)s "%(a)s" %(D)s`. It retains Gunicorn's connection peer address, log time, response status, response bytes, User-Agent, and microsecond duration. It deliberately excludes request method/protocol together with every automatic URL-bearing atom; those extra fields are not required by the acceptance criteria. `%(h)s` may identify a proxy peer rather than the originating browser, and `%(a)s` remains arbitrary caller-supplied text. No request/response header atom, environment atom, custom logger, or new dependency is added.

Proposed paths:

- `gunicorn.conf.py` — replace only `access_log_format`.
- `tests/test_access_logging.py` — add the isolated stdlib `unittest` regression using real Gunicorn atoms and the repository configuration.
- `README.md` — document the retained fields, removed URL observability, and caller-controlled User-Agent limitation.
- `odd/tasks/auth-security-remediation.md` — preparation and eventual RED/GREEN/REFACTOR closure evidence.

TDD and checks: first add the focused regression while production configuration is unchanged and record a genuine RED caused by the token-bearing request line/referrer. Then make the one-format production change, record GREEN, assess REFACTOR without expanding the logging design, and run:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_access_logging.py' -v
venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
venv/bin/python -m pip check
git diff --check
```

The focused test uses `runpy` to load `gunicorn.conf.py`, in-memory response/request/environment doubles, `gunicorn.glogging.Logger.atoms`, and `SafeAtoms`; it does not construct Gunicorn's logger, start Gunicorn or Flask, open a listening socket, use the database, contact Redis/SMTP/network services, or write a real log file.

Rollback boundary: revert only the `access_log_format` change, the isolated `tests/test_access_logging.py` regression, A3 README text, and A3 tracker updates. Restoring the previous format knowingly restores request-target and referrer disclosure. A2a/A2b behavior and tests remain untouched.

#### A3 implementation evidence

The three-test regression was written first while `gunicorn.conf.py` still used the previous combined format. Focused RED:

```text
Ran 3 tests in 0.005s
FAILED (failures=4)
```

Two subtest failures proved that the configured format still contained `%(r)s` and `%(f)s`. Two more proved that formatting real Gunicorn atoms emitted the distinct `raw-request-marker` and `referrer-marker`. The direct `%(U)s`/`%(q)s` exclusions already passed, while source-sensitivity assertions confirmed their distinct path/query markers were present in the real atoms. The retained-metadata test also passed.

After the one-line `access_log_format` replacement, focused GREEN:

```text
Ran 3 tests in 0.004s
OK
```

REFACTOR assessment found no production structure to change: the fix remains one configuration value, and the regression already separates format policy, real-atom source sensitivity, and retained metadata. No source-mutating normalizer was applicable, so no unrelated formatting was introduced.

Final verification after documentation and tracking updates:

```text
Focused: Ran 3 tests in 0.005s — OK
Full suite: Ran 24 tests in 2.661s — OK
Dependency check: No broken requirements found.
Diff check: passed with no output
```

Commit-closure verification after independent approval ran the full `24/24` tests in `2.678s`, reported `No broken requirements found.`, and passed both unstaged and staged diff checks without output.

Observed authored size versus exact base `cce12c852327f732edd53fb69a8a23fb063cf885`: 203 additions and 32 deletions (`235` authored changed lines): `README.md` 6/0, `gunicorn.conf.py` 1/1, `odd/tasks/auth-security-remediation.md` 99/31, and `tests/test_access_logging.py` 97/0. The regression is counted from its full 97-line readback rather than omitted by tracked-only diff statistics.

Scope and limitation: this work changes only Gunicorn's configured access-line fields, its isolated regression, README observability guidance, and this tracker. It makes no claim about arbitrary secrets placed in the retained User-Agent, application/security logs, upstream proxy logs, or other logging systems. No dependency, custom logger, auth behavior, schema, runtime service, or Spanish UI copy changed.

Independent closure verification:

- The parent read back the source, configuration, regression, and tracker; reran the three focused tests with `3/3` passing in `0.006s`; and confirmed `git diff --check` was clean.
- The parent's read-only risk classification was unavailable/high only because the preserved untracked `.atl/` and `.codegraph/` inventory prevents classification. It was not retried, no native lifecycle started, and clone-local RDD remained off.
- Independent verifier `ses_f31d0a13fffeMW95eAIeXBBT50` confirmed the production delta is one access-format line across the exact four-path scope; a Gunicorn 23 atom probe excluded request line, direct path, query, and referrer while retaining all six required metadata fields; focused `3/3` passed in `0.005s`, full `24/24` passed in `2.606s`, and both dependency and diff checks passed.
- Verifier fingerprints confirmed no verification-time file mutation. The retained caller-controlled User-Agent boundary, other-log exclusions, and no-live-server limitation remain explicit and non-blocking.

## Delivery ledger

```text
main @ 9366058 (A1+CI already integrated)
  └─ A2a fix/auth-reset-origin @ 668e74a (local closure and A2b base)
       └─ A2b fix/auth-reset-tokens @ cce12c8 (local behavior and tracking closure)
            └─ A3 fix/auth-log-redaction @ cce12c8 base (authorized local work in progress)
```

- Strategy: `stacked-to-main`; `ask-on-risk` applies to A3, while A2b remains historically `exception-ok` under its explicit bounded approval.
- Integration order remains sequential to `main`: A1, then A2a, then A2b, then A3. A1 is integrated; A2a/A2b/A3 publication and integration remain unauthorized.
- Initial authored running line count: `646` for A1 code, tests, and README.
- Behavior commit: `213ef59f4400d178bb06ba74fe06cbd42d5e70dc`, with `697` additions and `180` deletions overall.
- Behavior commit tracking overhead: `231` additions for this task document, separate from the `466` additions and `180` deletions (`646` authored lines) in A1 code, tests, and README.
- Tracking-only closure commit: separate from behavior; its immutable SHA is reported externally to avoid self-referential hashing.
- Tracking-only closure delta: `11` additions and `7` deletions in this document.
- Original full forecast: approximately `590–820` authored additions plus deletions.
- Revised A2 slice forecasts before materialization: A2a `370–390 ±30` and A2b `360–410`, each including tracker changes. A2b materialized at the exact observed size recorded below.
- A2b pre-closure authored size: 429 additions and 54 deletions (`483` authored lines), with the exact per-path counts recorded above. Explicit `size:exception` is approved only for this cohesive A2b work unit; final post-closure size is recorded below.
- A2b behavior commit: `fe3773e98eee80c15f89410dc94e8e87afd09740` (`436` additions, `57` deletions, `493` authored lines).
- A2b tracking closure and exact A3 base: `cce12c852327f732edd53fb69a8a23fb063cf885`.
- A3 implementation passed parent and independent verification; local commit closure is authorized without RDD or native approval.
- A2b final authored size after tracking closure: 440 additions and 58 deletions (`498` authored lines): `README.md` 16/0, `app/models.py` 120/14, `app/routes/auth_routes.py` 20/7, `odd/tasks/auth-security-remediation.md` 108/36, and `tests/test_auth_reset.py` 176/1.
- Work-unit approximately 400-line heuristic: advisory only; PR budget `400` still applies.
- Size handling: the original full-A2 snapshot was split once into cohesive A2a/A2b review units. The A1+CI exception did not transfer; the maintainer separately approved `size:exception` for this exact A2b local closure. No exception transfers to A2a or A3, and no review approval transfers to A2b. Do not shrink content artificially or omit tests/docs.
- Historical full-A2 snapshot: `fix/auth-reset@0491c02724a612065eb39768e64377a085e108f5`, natively approved with its receipt acknowledged/burned. It is preserved untouched and supplies no review inheritance.
- A2a behavior commit: `8a7fc4169f48a4976352fc6a1bda639ddb1fff20` (`430` authored changed lines including prepared tracking). Final A2a slice: `478` authored changed lines versus `main@9366058123bf60705804a70715dea63e763abde1`, including the tracking-only closure.
- A2a tracking closure and exact A2b base: `668e74abfe5db1d2672d088c6373d7c3d3867bef`. A2a native review and acknowledgement are closed; their lineage and evidence do not transfer to A2b.
- A1 behavior `213ef59f4400d178bb06ba74fe06cbd42d5e70dc`, tracking closure `636649426e87cc1b9519907ffab8de2c7646f833`, CI `9f5723c1c431d2f15b3c676fc4058f1dc22cad89`, publication proof `d76821939046ef8aa68e8a23fd69e5c78e76bac3`, and integration preparation `92c2d3d852944e0c3bdb8a229e118f7b75dcf43b` were integrated by PR #1 as merge `972f158f4ba9147b7d4dd2f12acb3cb9f5cbe518`.
- Existing untracked `.atl/` and `.codegraph/` directories must remain untouched.

## Known limitations and blockers

- The ignored Python 3.11 virtual environment and existing requirements are installed locally; no global packages or dependency files changed.
- No pre-existing automated tests, test runner configuration, or management CLI existed before A1.
- The canonical production origin value is an environment/deployment input; tests must use an isolated non-routable value.
- `PasswordResetRequestForm` uses WTForms `Email`; A2a explicitly declared `email-validator==2.2.0` in `requirements.txt`. A2b adds no dependency and needs no installation.
- A1 used only Flask's isolated test client/CLI and in-memory SQLite. No live database, Redis, SMTP, external service, or deployment environment was contacted.
- A2b's SQLite tests verify the application-level conditional update and failure handling but do not constitute PostgreSQL concurrency proof.
- Gunicorn is pinned at `23.0.0`. Its `%(r)s` atom is built from `RAW_URI`, `%(U)s` from `PATH_INFO`, `%(q)s` from `QUERY_STRING`, and `%(f)s` from the caller-supplied `Referer` header. Removing those atoms prevents automatic URL leakage from this Gunicorn access format, not arbitrary secrets supplied through retained fields or other application/proxy logs.

## Progress and next step

- A1: COMPLETED and integrated through PR #1 at `main@972f158f4ba9147b7d4dd2f12acb3cb9f5cbe518`
- A2a: CLOSED locally at `668e74abfe5db1d2672d088c6373d7c3d3867bef`; native review approved and acknowledgement consumed
- A2b: CLOSED locally at `cce12c852327f732edd53fb69a8a23fb063cf885` under the explicit A2b-only size exception; independently verified, with no native approval
- A3: INDEPENDENTLY VERIFIED on `fix/auth-log-redaction` from exact base `cce12c852327f732edd53fb69a8a23fb063cf885`; local commit closure is authorized and in progress without native approval
- Mirror gate: COMPLETED; the repository document and full Engram observation `#5379` were read back after replacing the prior incomplete summary.
- **Next step:** run the final local checks, stage exactly the four A3 paths, create the cohesive behavior commit, record its SHA in this tracker, and create one tracking-only closure commit. Then mirror/read back the full tracker and stop. The DIRECT writer must not run risk assessment or native review and must not perform publication, push, PR, merge, remote application action, deployment, production service contact, tool/model configuration change, or unrelated auth work.
