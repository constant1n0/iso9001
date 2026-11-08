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
- **KN1** [S1–S3, S5] delegated (context backstop) — implemented, independent verification pending (risk items 1, 2) — Core knowledge register: model with type (knowledge/lesson), title, summary, source text, owner Person (active, RESTRICT), optional next review date, retired flag; migration from `d7f9a1b3c5e7`; service (crud.Spec/Register), policy Resource, web screens with type/overdue/retired filters, nav entry, MCP module, docs, tests.
- **KN2** [S4] delegated (context backstop) — pending — Links: processes, controlled documents, expert people, training records, competence requirements (many-to-many), and one optional typed lesson origin (nonconformity, audit finding, improvement, review action); audited association snapshots; screens, MCP, docs, tests. Depends on KN1.

## Log
- **L1** Original user request: «si» to «¿Empezamos con el siguiente módulo de la Fase 3, conocimiento (7.1.6)?».
- **L2** Read-only map (explorer): no existing knowledge register; reuse Person (`app/models.py:410`), competence (`:493`, `:516`), training (`:856`), documents (`:2103`, `:2154`), NC/CAPA (`:627`, `:695`), improvements (`:2443`), audit findings (`:1002`), processes (`:207`). Equipment module is the implementation pattern (crud.Spec, policy Resource, blueprint, MCP Module, named constraints, non-native enums). Migration head `d7f9a1b3c5e7`.
- **L3** Product decisions S2–S5 taken by the user in one questionnaire. Design assumptions (not user requirements): "origen" free text plus optional typed origin link; retirement keeps history; deletion only for uncited items by administrators; links do not change the linked records.
- **L4** Route: the parent context passed the ~150k backstop, so each unit goes to one bounded writer; units are dependent, so they run sequentially, not in parallel. Risk items: 1 (new migration, new tables only), 3 (new MCP module = additive). Applicable test-first policy with the runner above.
- **L5** User selected «Dos PR encadenados a main»: «Primero un PR con KN1 contra main; cuando esté fusionado, otro con KN2.» Slice boundaries: KN1 on `feat/organizational-knowledge`; KN2 on a later branch from the merged main.
- **L6** KN1 writer result (S1–S3, S5 covered; S4 deferred to KN2): migration `e8a0c2d4f6b8` (down `d7f9a1b3c5e7`), MCP slug `knowledge`, UI `/conocimientos/`, policy `Resource.KNOWLEDGE: Grant(_ALL, _ADMIN_AUDITOR, _ADMIN)`. Observed RED→GREEN per rule. Writer checks: knowledge 15 OK/1 skip; migrations 30 skipped (no PostgreSQL); MCP 99 OK; full suite 1,916 OK (110 skips). Parent spot check: diff only on allowed surfaces; knowledge tests OK (1 skip). Source text is optional; required: type, title, summary, owner.
