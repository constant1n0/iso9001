# GitHub Actions CI

Repository locator: `odd/tasks/github-actions-ci.md`

## Objective and boundary

Add one deterministic GitHub Actions check for the existing Python 3.11 `unittest` suite, plus concise maintainer documentation. The preparation boundary is `fix/auth-bootstrap` at `636649426e87cc1b9519907ffab8de2c7646f833`; no implementation existed at that boundary.

## Authorization and scope

- **Included:** `.github/workflows/ci.yml`, CI usage/required-check guidance in `README.md`, and the existing applicable tests.
- **Historical remote state:** the parent pushed the CI work unit to `origin/fix/auth-bootstrap` and used the configured GitHub CLI session only to read its Actions run/logs in `constant1n0/iso9001`; at that time no PR, merge, or settings mutation was authorized.
- **Current remote authorization:** the user subsequently authorized the parent to require `Python 3.11 tests`, open the exact A1+CI PR, and merge it into `main` without bypass after PR-specific CI passes.
- **Still excluded:** deployment, live PostgreSQL/Redis/SMTP, new credentials or sessions, dependency upgrades, scanners, auth A2/A3, and any other branch or feature.
- Existing untracked `.atl/` and `.codegraph/` remain untouched.
- The auth tracker mirrors the historical publication and current bounded CI2 authorization; neither tracker expands beyond the user's explicit scope.

## CI1 — Add the test workflow and maintainer guidance

- **Status:** COMPLETED; locally and GitHub-hosted verified.
- **Route:** `delegated` because native-library setup, workflow security, docs, and platform verification form one cross-cutting unit. Preparation and implementation were delegated.
- **Forecast:** about 55–90 authored additions plus deletions, one cohesive work unit; the ~400-line review threshold is advisory, not a compression target.
- **Behavior commit:** `9f5723c1c431d2f15b3c676fc4058f1dc22cad89`, published to `origin/fix/auth-bootstrap`.

Implemented workflow contract:

- Workflow `CI`; stable job key `tests`; stable check name `Python 3.11 tests`.
- Trigger every `push`, `pull_request` targeting `main`, and optional `workflow_dispatch`; never use `pull_request_target`.
- `ubuntu-24.04`, Python `3.11`, `actions/checkout@v6` with `persist-credentials: false`, and `actions/setup-python@v7` with pip caching.
- Top-level `permissions: contents: read`, workflow concurrency by workflow/ref with stale runs cancelled, and a 15-minute job timeout.
- Install only the pinned `requirements.txt` and WeasyPrint 63.0 runtime packages: `libpango-1.0-0`, `libharfbuzz0b`, `libpangoft2-1.0-0`, `libharfbuzz-subset0`.
- Run `python -m unittest discover -s tests -p 'test_*.py' -v`; no secrets or service containers.
- Document the local command and that Actions checks are advisory until a maintainer separately configures `Python 3.11 tests` as a required check.

Acceptance criteria:

- [x] Workflow syntax parses locally and the contract below passes.
- [x] The unchanged 10-test suite passes before and after the change with isolated SQLite/environment/logging behavior.
- [x] No workflow token persists after checkout and permissions remain read-only.
- [x] No dependency file, application behavior, auth tracker, or unrelated path changes.
- [x] README names the stable check and separates CI availability from merge enforcement.
- [x] The authorized GitHub-hosted run completed successfully for the exact behavior commit.

## CI2 — Protect `main` and integrate the published A1+CI branch

- **Status:** PENDING / AUTHORIZED; the parent owns every bounded Git/`gh`/API mutation.
- **Route:** `inline-parent` for repository settings, PR checks, merge, and hosted-CI readback; `delegated` for multi-file tracker updates and preparation evidence.
- **Repository:** `constant1n0/iso9001`; use only the existing configured Git/`gh` authentication.
- **Pre-update boundary:** base `main@07159e95de7686f5c2f0cedb0f63af332687c93e`; published head `fix/auth-bootstrap@d76821939046ef8aa68e8a23fd69e5c78e76bac3` before these tracker-only edits.
- **Baseline:** no PR exists; `main` is unprotected (`404 Branch not protected`), with effective branch rules `[]` and rulesets `[]`.
- **Pre-update review size:** `1,043` additions plus deletions: `708` code/tests/README/workflow and `335` task-tracker lines. Later count growth from this preparation is documentation-only.
- **Hosted head proof:** [run 35523106948](https://github.com/constant1n0/iso9001/actions/runs/35523106948) succeeded for exact head `d76821939046ef8aa68e8a23fd69e5c78e76bac3`.
- **Required check identity:** context `Python 3.11 tests`, GitHub Actions `app_id: 15368` (`github-actions`).
- **Review decision:** the maintainer explicitly approved `size:exception` for this cohesive A1+CI PR only; tests, protection, and verification remain mandatory.

Execution checklist:

- [ ] Configure `main` protection with `required_status_checks.strict: true`, check `{context: "Python 3.11 tests", app_id: 15368}`, and `enforce_admins: true`.
- [ ] Read the protection back through the API and confirm strict mode, context, app ID, admin enforcement, no force pushes/deletions, and no reviewer-count requirement or unrelated rule.
- [ ] Open one PR from `fix/auth-bootstrap` to `main`, documenting the approved `size:exception`, A1+CI boundary, four commits, verification, rollback, and A2/A3 exclusions.
- [ ] Confirm the PR's actual base SHA, head SHA, changed-path inventory, and changed-line count before merge; any change after `d768219` must be limited to these authorized tracker updates.
- [ ] Wait for PR-specific `Python 3.11 tests` on the PR's actual head SHA; do not substitute the earlier push run for `d768219`.
- [ ] Merge without `--admin`, bypass, force push, or branch deletion; preserve both branches.
- [ ] Read back `main`'s exact resulting SHA and require a successful hosted `Python 3.11 tests` run for that exact SHA.

Protection rollback requires separate user authorization. Never weaken or disable protection to make this PR mergeable; stop and report any mismatch instead.

## Verification and TDD evidence strategy

TDD is explicitly **on**. A missing workflow is not a behavioral RED, so no artificial missing-file failure will be claimed. For this declarative change, record the pre-change application suite as the behavioral baseline, validate YAML syntax and the security/runner contract locally, then treat the first authorized hosted run as end-to-end platform proof. If a real initial config defect fails validation, preserve that meaningful RED before correction.

```bash
ruby - .github/workflows/ci.yml <<'RUBY'
require "yaml"
workflow = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: false)
triggers = workflow["on"] || workflow[true] # Psych uses YAML 1.1 booleans.
raise "trigger contract" unless triggers.key?("push") && triggers.key?("pull_request") && triggers.key?("workflow_dispatch")
raise "PR contract" unless triggers.dig("pull_request", "branches") == ["main"]
raise "permissions contract" unless workflow["permissions"] == {"contents" => "read"}
raise "concurrency contract" unless workflow.dig("concurrency", "cancel-in-progress") == true && workflow.dig("concurrency", "group") == "${{ github.workflow }}-${{ github.ref }}"
job = workflow.dig("jobs", "tests")
raise "job contract" unless job && job["name"] == "Python 3.11 tests" && job["runs-on"] == "ubuntu-24.04" && job["timeout-minutes"] == 15
raise "services contract" if job.key?("services")
steps = job.fetch("steps")
raise "checkout contract" unless steps.any? { |step| step["uses"] == "actions/checkout@v6" && step.dig("with", "persist-credentials") == false }
raise "Python contract" unless steps.any? { |step| step["uses"] == "actions/setup-python@v7" && step.dig("with", "python-version") == "3.11" && step.dig("with", "cache") == "pip" && step.dig("with", "cache-dependency-path") == "requirements.txt" }
runs = steps.map { |step| step["run"] }.compact
native = %w[libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libharfbuzz-subset0]
apt = runs.find { |run| run.include?("sudo apt-get install --yes --no-install-recommends") }
raise "native package contract" unless apt && apt.include?("sudo apt-get update") && native.all? { |package| apt.include?(package) }
raise "pip contract" unless runs.include?("python -m pip install --disable-pip-version-check --requirement requirements.txt")
raise "test contract" unless runs.include?("python -m unittest discover -s tests -p 'test_*.py' -v")
raise "interpolation contract" if runs.any? { |run| run.include?("${{") }
raise "action contract" unless steps.map { |step| step["uses"] }.compact.sort == ["actions/checkout@v6", "actions/setup-python@v7"]
has_key = lambda { |value, key| value.is_a?(Hash) ? (value.key?(key) || value.values.any? { |item| has_key.call(item, key) }) : (value.is_a?(Array) && value.any? { |item| has_key.call(item, key) }) }
raise "secrets contract" if has_key.call(workflow, "secrets")
puts "Workflow contract OK"
RUBY
venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
git diff --check
git diff -- .github/workflows/ci.yml README.md
git status --short --branch
```

`actionlint`, `yamllint`, PyYAML, and `jsonschema` are not locally available; do not install them for this unit. The local Ruby/Psych combination lacks `YAML.safe_load_file` and `Array#filter_map`, so the compatible validator uses `YAML.safe_load(File.read(...), aliases: false)` plus `map`/`compact`. After an authorized parent push, the parent may use the configured `gh` session only for read-only Actions run/log inspection.

### Local implementation evidence

- Baseline: 10 tests passed in `0.733s`; the existing Flask-Caching deprecation warning remained non-fatal.
- Validator harness compatibility errors (not application or workflow RED): Ruby 2.6.10 lacks `YAML.safe_load_file` and `Array#filter_map`; Psych 3.1.0 is the YAML library version. The compatible checker above then printed `Workflow contract OK`.
- Final: 10 tests passed in `0.718s`; `git diff --check` produced no output.
- Independent verification: the contract printed `Workflow contract OK`, 10 tests passed in `0.724s`, `git diff --check` was clean, and the workflow, tracker, and README diff were accepted.
- Parent verification: 10 tests passed in `0.722s`; the parent read the full workflow, tracker, and README diff.
- Native risk assessment was unavailable because the tool refused the existing undeclared untracked inventory. Risk remains conservatively high; the independent local verification passed. RDD stayed off with no invocation or receipt.
- The original CI behavior commit contained 158 additions: 49 workflow, 13 README, and the 96-line tracker. The workflow was read in full after writing.
- Hosted proof: [run 35522042241](https://github.com/constant1n0/iso9001/actions/runs/35522042241) completed successfully for head `9f5723c1c431d2f15b3c676fc4058f1dc22cad89`; job `Python 3.11 tests` and every step succeeded.
- The hosted runner used Python 3.11.16 on Ubuntu 24.04, installed the native and pinned Python dependencies successfully, and ran 10 tests in `1.391s` with `OK`; only the existing non-fatal Flask-Caching warning remained. `gh run watch --exit-status` succeeded.
- No PR was created. The tracking-only closure commit is reported outside this document to avoid self-referential commit metadata.

## Rollback, enforcement, and next step

CI1 rollback removes only `.github/workflows/ci.yml` and the matching README CI guidance; application behavior and tests remain intact. CI2 protection rollback is a separate remote mutation and requires separate user authorization.

**Next step:** CI1 is complete and CI2 is authorized but pending parent execution. The parent must apply and read back protection, open and verify the exact PR, wait for PR-specific CI, merge without bypass, and verify exact-SHA `main` CI. Auth A2/A3 remain pending and out of scope.
