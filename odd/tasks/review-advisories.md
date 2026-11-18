# Review advisories follow-up

Objective: close the non-blocking advisories left by the Phase 3 reviews and verifiers in one small corrections PR. Branch: `fix/review-advisories` from `300cd32` · Delivery: single PR (≤ ~600 changed lines) · Runner: `/home/dcm/workspace/iso9001-worktrees/fixes-2026/venv/bin/python -m unittest discover -s tests -p '...'` (PostgreSQL tests skip locally; CI runs them).

## Specs

- **S1** — User selected «Los siete avisos pequeños — recomendado»: «Un PR de correcciones: escapar el filtro de lote, mejorar la documentación MCP del historial de cambios, permitir recuperar propuestas con órdenes canceladas, mensajes de validación más claros, tests de filtros más estrictos y tests de concurrencia más precisos. El de borrar NC en SQLite se revisa por si no aplica.»
- Advisories in scope (only their id and location were recorded; each must be re-diagnosed against the current code before changing anything, and dropped with a reason if it does not reproduce):
  - **A1** R3-lot-filter-escaping — `app/services/production.py:224-225`: lot text filter, likely unescaped LIKE wildcards (`%`, `_`).
  - **A2** R3-mcp-history-documentation — `docs/mcp.md:118-119`: MCP documentation of production change history.
  - **A3** R4-cancelled-proposal-recovery — `app/services/production_changes.py:76-77`: recovering a proposal whose affected orders were cancelled.
  - **A4** R4-validation-feedback — `app/routes/production_routes.py:150`: validation feedback on a production form.
  - **A5** R3-filter-assertions — `tests/test_production_property_screens.py:50`: list-filter tests assert weakly.
  - **A6** R3-citation-contention-coverage — `tests/test_design_io.py:110`: concurrency coverage of design input citations.
  - **A7** Release contention tests — `tests/test_product_release_concurrency.py:69-86` and `tests/test_product_release_decision_concurrency.py:88`: a blocker-PID assertion cannot tell which row lock caused the wait.
  - **A8** SQLite NC-delete — with SQLite foreign keys off, deleting a cited nonconformity can leave dangling citations because `nonconformities.delete` relies on database enforcement (pre-existing). Review only: fix if cheap and safe, otherwise document why it does not apply (production runs PostgreSQL).
- Assumption: no change to existing user-visible behaviour beyond what each advisory requires; no migration.

## Tasks

- **T1** [S1, A1–A8] delegated (context backstop) — done (commits e9489e1, d5d8142; native review approved; independently verified, L3) — re-diagnose each advisory, fix or drop with reason, tests per fix.

## Log

- **L1** Original user request: «si» to «¿Sigo con alguno?», then selected «Los siete avisos pequeños — recomendado» (2026-11-18). Production runs 300cd32 at f1b3d5e7a9c2.
- **L2** Writer result: A1 fixed (lot LIKE filter escapes `%`, `_` and `\`; «A_1» returned 4 rows before, 1 after); A2 fixed (docs/mcp.md: all change history readable, only proposals writable); A3 dropped — already supported: proposals whose affected orders were all cancelled can have them replaced or be rejected (regression test added); A4 fixed (actionable Spanish feedback on repeated/contradictory production form selections, 400 and input kept); A5 fixed (exact included/excluded rows in property filter tests); A6 fixed (design citation lock, ordering, refresh and post-wait checks); A7 fixed (release contention tests assert the blocked query is the intended row-locking SELECT); A8 fixed (nonconformities.delete checks its seven RESTRICT citations before deleting, also with SQLite FKs off; existing conflict message kept). 368 changed lines; RED observed for A1, A4, A8. Writer checks: production 109 OK/5 skips, release 42 OK/2, design 107 OK/6, NC 93 OK, advisories 6 OK, MCP 99 OK, full 2,267 OK (129 skips).
- **L3** Independent verifier: A1, A2, A3 (drop confirmed), A5, A6, A7, A8 met (A8 exercised all eight RESTRICT citation types with SQLite FKs on and off, zero audit writes on refusal, uncited deletes identical to baseline). Two blockers in A4, fixed by the parent: B1 the new hint was flashed from the shared `_refused`, changing the duplicate-field feedback of the property, post-delivery, change and evidence routes; it is now shown only on the order form, and only when a field or option is actually repeated. B2 the order form's permanent help text changed for every visit; reverted. Also corrected the MCP module catalogue note (it said MCP reads proposals only; it reads every change). Probes committed as tests/test_review_advisories_independent.py (SHA-256 e1374637…), which failed on e9489e1 and pass now: advisories 9 OK, production 109 OK/5 skips, release 42 OK/2 skips, MCP 99 OK.
- **L4** User selected «Revisión nativa y luego publicar» (assess: medium, not due). Native review `review-455e7ce19666b6c7` (risk, resilience, reliability) on 300cd32..d5d8142 (12 paths, 494 lines): APPROVED without findings and acknowledged.
- **L5** First CI on PR #206 (run 44370767110) failed in the two PostgreSQL release contention tests strengthened by A7: `pg_stat_activity.query` is truncated at 1,024 characters, so the regexes on `FROM`/`FOR UPDATE` never saw those clauses of the wide SELECT. The assertion now checks the waiting query is a SELECT naming `<table>.id` and that the waiter holds the `tuple` lock on that table in `AccessExclusiveLock` mode (FOR UPDATE; FK checks take AccessShareLock, unique-index waits take none). Checked on a disposable local PostgreSQL 16.13 container: release concurrency 2 OK, all `*concurrency*` 20 OK, design_io 13 OK, advisories 9 OK, no skips; removing the order lock in a scratch copy makes the creation test fail (it then waits on the INSERT). Not merged until CI is green.
