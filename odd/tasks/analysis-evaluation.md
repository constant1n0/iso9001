# Analysis and evaluation (ISO 9001 9.1.3 a and e)

Objective: give management review and the monthly report the analysis of product/service conformity (9.1.3 a) and of the effectiveness of actions taken to address risks and opportunities (9.1.3 e). Branches: one per slice from the then-current main (`feat/conformity-analysis` for T1) · Baseline: `ca3c450` · Delivery: `stacked-to-main` (two slices, each ≤ ~1,000 changed lines) · Runner: `/home/dcm/workspace/iso9001-worktrees/fixes-2026/venv/bin/python -m unittest discover -s tests -p '...'` (PostgreSQL tests skip locally; CI runs them).

## Specs

- **S1** — User: «si» to starting «9.1.3 a)», described as «el análisis de la conformidad de productos y servicios a partir de las liberaciones». Gap analysis `docs/analisis-brechas-iso-9001.md:147` (9.1.3 row, P): «Sin datos de la conformidad de los productos y servicios (9.1.3 a)» and «Sin un análisis conjunto de la eficacia de las acciones tomadas para abordar los riesgos (9.1.3 e): cada acción registra la suya (`AccionRiesgo.eficacia`), pero ni la revisión ni el informe la resume».
- **S2** — Selected «Revisión por la dirección e informe mensual — recomendado»: «Nueva entrada del periodo en la revisión (9.3.2 c) y un apartado en el informe mensual, igual que proveedores.»
- **S3** — Selected «Liberaciones y sus verificaciones — recomendado»: «Liberaciones decididas (liberadas, con concesión, rechazadas), % de verificaciones conformes y no conformidades de producción del periodo.»
- **S4** — Selected «Por proceso y por cliente — recomendado»: «Totales del periodo y tablas por proceso de la orden y por cliente.»
- **S5** — Selected «Sí, también 9.1.3 e)»: «Añadir a la revisión un resumen de las acciones de riesgo del periodo por eficacia (eficaz / no eficaz / sin evaluar).»
- Assumptions (not user requirements): a release belongs to the period by its decision date; «no conformidades de producción» are nonconformities cited by production records, property incidents, post-delivery activities or release-related records of the period (writer to define precisely from existing links and state it); percentages are omitted (shown as «—») when there is nothing to divide; read-only analysis, no new tables or migration; the monthly report gets 9.1.3 e) too only if it already summarises risks (otherwise review only, per S5's wording).

## Tasks

- **T1** [S1, S2, S3, S4] delegated (context backstop) — implemented, independent verification pending (risk item 1: frozen review inputs) — conformity input in management review inputs (period-bounded, totals + by process + by customer) and the monthly report section; docs.
- **T2** [S1, S5] pending — risk/opportunity action effectiveness summary in management review inputs (eficaz / no eficaz / sin evaluar for actions of the period); docs; gap analysis 9.1.3 → C when both are done.

## Log

- **L1** Original user request: «si» to «Solo queda 9.1.3 a)… Dime si lo empezamos.» (2026-11-20). Production runs ca3c450 at f1b3d5e7a9c2.
- **L2** Product decisions S2–S5 taken by the user in one questionnaire (2026-11-20). Two slices T1–T2.
- **L3** T1 writer result (S1–S4): `app/services/conformity_analysis.py` (read-only) feeds a new management review input next to providers (screen, minutes, frozen snapshot, MCP previews) and a monthly report section (the report has no provider section; follows the corrective-action/objective pattern). Definitions: releases by inclusive decision_date, drafts excluded; all verifications of those decided releases; production NCs = distinct NCs cited by controls, preservation records and property incidents dated in the period plus completed post-delivery activities by result date; groups by the order's current process/customer («Sin orden»/«Sin cliente»), an NC counted once overall and once per group; «—» for empty denominators. Parent-path correction: the writer stopped because the parent listed wrong template paths; the parent re-derived them (app/templates/revisiones_direccion/*.html, app/templates/reportes/reporte_mensual.html). 638 changed lines; RED observed per rule. Writer checks: conformity 8 OK, management review 58 OK/2 skips, reports 8 OK, release 42 OK/2, MCP 100 OK, full 2,281 OK (129 skips).
