# Organizational Knowledge — Phase 3, second delivery (ISO 9001 7.1.6)
Branch: `feat/organizational-knowledge` · Baseline: `2a47bc0` · Delivery: ask-on-risk → `stacked-to-main` (forecast ~1,300 authored lines). Slice 1 = KN1 (PR against main); slice 2 = KN2 (new branch from main after slice 1 merges).
Runner: `/home/dcm/workspace/iso9001-worktrees/fixes-2026/venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`

## Specs
- **S1** — User: «si» to starting the next Phase 3 module, «conocimiento (7.1.6)». Gap analysis `docs/analisis-brechas-iso-9001.md:131`: «Sin registro de conocimientos ni de lecciones aprendidas»; `:179` proposes «un registro de conocimientos o de lecciones aprendidas».
- **S2** — Selected «Conocimientos y lecciones — recomendado»: «Un único registro con un tipo: conocimiento de la organización (saber hacer, fuentes externas) o lección aprendida. Cada elemento tiene título, resumen y origen.»
- **S3** — Selected «Responsable y revisión opcional — recomendado»: «Cada elemento tiene una persona responsable activa y, si se quiere, una próxima fecha de revisión. Se puede filtrar por revisiones vencidas. Los elementos que dejan de usarse se retiran, no se borran.»
- **S4** — Selected all links:
  - «Procesos y documentos»: «Los procesos afectados y los documentos controlados donde está el conocimiento por escrito.»
  - «Personas expertas»: «Personas que tienen ese conocimiento, además del responsable.»
  - «Origen de la lección»: «La no conformidad, el hallazgo de auditoría, la mejora o la acción de revisión de la que sale una lección. Se registra a mano y no se crea nada automáticamente.»
  - «Formación y competencias»: «Las formaciones y los requisitos de competencia que sirven para transmitir ese conocimiento.»
- **S5** — Selected «Como equipos — recomendado»: «Todos los roles leen. Administradores y auditores crean y editan. Solo un administrador puede borrar, y solo elementos que nadie cita. MCP sigue las mismas reglas y nunca borra.»

## Tasks
- **KN1** [S1–S3, S5] delegated (context backstop) — done (commits 29a4e07, 87d262d; PR #182 merged 8c2e55e; see L7; risk items 1, 2 independently verified) — Core knowledge register: model with type (knowledge/lesson), title, summary, source text, owner Person (active, RESTRICT), optional next review date, retired flag; migration from `d7f9a1b3c5e7`; service (crud.Spec/Register), policy Resource, web screens with type/overdue/retired filters, nav entry, MCP module, docs, tests.
- **KN2** [S4] delegated (context backstop) — done (commits ac956be, f03c6ec; PR #183 merged 4467bc4; risk items 1, 2 independently verified, L10) — Links: processes, controlled documents, expert people, training records, competence requirements (many-to-many), and one optional typed lesson origin (nonconformity, audit finding, improvement, review action); audited association snapshots; screens, MCP, docs, tests. Depends on KN1.

## Log
- **L1** Original user request: «si» to «¿Empezamos con el siguiente módulo de la Fase 3, conocimiento (7.1.6)?».
- **L2** Read-only map (explorer): no existing knowledge register; reuse Person (`app/models.py:410`), competence (`:493`, `:516`), training (`:856`), documents (`:2103`, `:2154`), NC/CAPA (`:627`, `:695`), improvements (`:2443`), audit findings (`:1002`), processes (`:207`). Equipment module is the implementation pattern (crud.Spec, policy Resource, blueprint, MCP Module, named constraints, non-native enums). Migration head `d7f9a1b3c5e7`.
- **L3** Product decisions S2–S5 taken by the user in one questionnaire. Design assumptions (not user requirements): "origen" free text plus optional typed origin link; retirement keeps history; deletion only for uncited items by administrators; links do not change the linked records.
- **L4** Route: the parent context passed the ~150k backstop, so each unit goes to one bounded writer; units are dependent, so they run sequentially, not in parallel. Risk items: 1 (new migration, new tables only), 3 (new MCP module = additive). Applicable test-first policy with the runner above.
- **L5** User selected «Dos PR encadenados a main»: «Primero un PR con KN1 contra main; cuando esté fusionado, otro con KN2.» Slice boundaries: KN1 on `feat/organizational-knowledge`; KN2 on a later branch from the merged main.
- **L6** KN1 writer result (S1–S3, S5 covered; S4 deferred to KN2): migration `e8a0c2d4f6b8` (down `d7f9a1b3c5e7`), MCP slug `knowledge`, UI `/conocimientos/`, policy `Resource.KNOWLEDGE: Grant(_ALL, _ADMIN_AUDITOR, _ADMIN)`. Observed RED→GREEN per rule. Writer checks: knowledge 15 OK/1 skip; migrations 30 skipped (no PostgreSQL); MCP 99 OK; full suite 1,916 OK (110 skips). Parent spot check: diff only on allowed surfaces; knowledge tests OK (1 skip). Source text is optional; required: type, title, summary, owner.
- **L7** Independent verifier: S1, S3, S5 met; S2 BLOCKER — editing a `leccion` through the rendered form silently saved it as `conocimiento` (`KnowledgeForm(obj=item)` handed the enum to a select offering enum names). Migration verified additive with all existing rows preserved (SQLite); all knowledge routes guarded; web/MCP denials leave DB hashes unchanged. One correction batch: the edit form now preselects `item.kind.name` on GET. Verifier probes committed as `tests/test_knowledge_independent.py`; observed RED (round-trip test failed) then GREEN: knowledge tests 19 OK/1 PG skip, MCP 99 OK. PostgreSQL execution still pending CI.
- **L8** Slice 1 published: user selected «Publicar y fusionar si sale verde» (native review explicitly not chosen for this candidate). PR #182 head 87d262d; CI pull_request 42533862362 and push 42533857849 both success (PostgreSQL migration executed); merged as 8c2e55e. Slice 2 (KN2) starts on `feat/knowledge-links` from 8c2e55e. Not deployed.
- **L9** KN2 writer result (S4 covered; S3/S5 preserved): migration `f9b1d3e5a7c9` (down `e8a0c2d4f6b8`); five many-to-many links (processes, documents, experts, training, competence requirements) and one optional typed origin (nonconformity, audit finding, improvement, review action) following the improvement typed-FK pattern with a CHECK of at most one. Assumption: origin allowed for both types. All targets RESTRICT; experts added to people REFERENCES; new links need active people/current documents, existing ones retained. Writer checks: knowledge 30 OK/1 skip, person 45 OK/5 skip, MCP 99 OK, full 1,932 OK (111 skips). Parent spot check: allowed surfaces only; knowledge 30 OK/1 skip. KN1 tests test_knowledge_constraints/test_knowledge_independent adjusted to compare the schema at their own revision.
- **L10** Independent verifier: S3, S4, S5 met, no blockers. Rendered-form no-op saves keep all links/origin without audit rows; rejected writes leave DB hashes unchanged; protected documents/origins not expanded; cited-target deletion refused with user-facing messages (documents have no delete route; FK tested directly); migration additive with existing rows preserved (SQLite). KN1 test edits only scope schema comparisons to their revision. Advisory (baseline, not change-caused): tests/test_ui_foundation.py needs git metadata and errors in a .git-free archive. Probes committed as tests/test_knowledge_links_independent.py (10 OK); knowledge tests 40 OK/1 PG skip. PostgreSQL execution pending CI.
- **L11** Slice 2 published: user selected «Publicar y fusionar si sale verde» (native review explicitly not chosen). PR #183 head f03c6ec; CI pull_request 42540750971 and push 42540746466 both success (PostgreSQL migrations executed); merged as 4467bc4. 7.1.6 complete.

## Production deployment (2026-11-09)

`main@4467bc4` (PRs #182 and #183) runs on `vulcano`. Authorized over SSH to `192.168.101.46` as `dcm`, no `sudo` by the agent.

| Step | Result |
|---|---|
| Preflight | Was `7fe0a9f`, clean, migration `d7f9a1b3c5e7`; four services active |
| Backups | `~/work/backups/calidad-2026-11-09-pre-4467bc4.dump` (579 entries, via `docker exec iso9001-db pg_dump`) and `iso9001-code-2026-11-09-pre-4467bc4.tar.gz`, mode 600 |
| Code | Incremental git bundle `7fe0a9f..main`, fast-forward to `4467bc4`; no dependency changes |
| Database | `flask db upgrade` `d7f9a1b3c5e7` → `e8a0c2d4f6b8` (knowledge register) → `f9b1d3e5a7c9` (knowledge links); `flask db check` clean |
| Restart (user, with sudo) | All four services, 2026-11-09 11:30 CET |
| Smoke tests | Services active, no journal errors; `/login` 200; `/equipos/`, `/conocimientos/` and `/conocimientos/nuevo` redirect to login; `/mcp` 401 Bearer |
