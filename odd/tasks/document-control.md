# Document Control

Repository locator: `odd/tasks/document-control.md` · Engram mirror: `odd/document-control/tasks`

## Objective

Control documented information (ISO 9001:2026 clause 7.5): every document has numbered revisions that go through review and approval before taking effect, the effective revision is available to everyone and cannot change, superseded revisions stay as obsolete evidence, files can be attached, documents are reviewed periodically and are withdrawn rather than deleted.

## Problem and why it matters

- `Document` is edited in place: `version` is free text, `approved_by` is free text, `signature` is unused and `content` is text only (`app/models.py`, `app/services/documents.py`).
- Only ADMIN can read documents (`Resource.DOCUMENTS: Grant(_ADMIN, _ADMIN, _ADMIN)`), so the people who must use them cannot see them (7.5.3.1 a).
- No review or approval record, no history of changes (7.5.2, 7.5.3.2 c), no periodic review, and documents can be hard-deleted (decision D3 of `qms-foundations` deferred soft delete to this change).
- Gap analysis Wave 1 item 1 (`docs/iso-9001-gap-analysis.md`).

## Authorized decisions

| ID | Decision | Source |
|---|---|---|
| Route | ODD, one delegated writer at a time; branch `feat/document-control` in worktree `iso9001-worktrees/user-management` from `main@420f499` | User: "todas, elige tú el orden"; scope choice (2026-10-10) |
| DC0 | Scope "Completo con ficheros" | User choice (2026-10-10) |
| DC1 | Revisions numbered automatically (1, 2, 3…); editing an effective document creates a new draft revision; at most one draft and one effective revision at a time | User choice |
| DC2 | Revision states `borrador` → `en_revision` → `aprobado` → `vigente` → `obsoleto`; rejection returns to `borrador` with a comment; publishing an approved revision makes it `vigente` (with its effective date) and the previous effective one `obsoleto` | User choice; publish step by the assistant |
| DC3 | Approver is ADMIN and never the revision's author; an effective or obsolete revision is immutable | User choice |
| DC4 | Every role reads the effective revision; drafts, revisions in review and approved-but-unpublished ones are visible to ADMIN and AUDITOR; ADMIN and AUDITOR author drafts | User choice; authoring roles by the assistant |
| DC5 | Each document has an owner (person) and a next review date; overdue reviews are flagged on the dashboard and in a notification | User choice |
| DC6 | Documents are withdrawn (`baja`, with date and reason), never hard-deleted; withdrawal obsoletes the effective revision | User choice; closes D3 |
| DC7 | Optional attachment per revision: PDF, DOCX, XLSX or ODT, at most 20 MB, validated by content (magic bytes) as well as extension, stored on the server outside the web root under a configurable directory, served only through an authorized route, with its SHA-256 recorded; immutable once the revision is effective; the storage directory is included in the backup procedure | User choice; storage details by the assistant |
| DC8 | Existing documents become revision 1 `vigente` with their current content, version text kept as a legacy label | User choice |
| Delivery | `auto-chain`, `stacked-to-main`; push, PR and merge when CI is green; standing Wave 1 `size:exception` (ask only above 1,500 lines); production deployment needs migrations, a storage directory and explicit authorization | User authorizations (2026-10-02, 2026-10-09) |
| Test adaptations | During Wave 1, existing test files that only need adapting to an approved design decision may join a writer's edit surface without asking; each one is reported in this tracker and in the PR. DC-1 adds `tests/test_ui_foundation.py`, `tests/test_web_authorization.py`, `tests/test_service_list_page.py`, `tests/test_ui_modulos.py`, `tests/test_delete_forms.py`, `tests/test_empty_optional_text.py` | User (2026-10-10) |
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [x] **DC-1 — Revisions and workflow (service).** Forecast 600-1,000.
  - `DocumentRevision` model and migration (existing documents → revision 1 `vigente`, DC8); `Document` gains owner, next review date, withdrawal fields; workflow service (create draft, edit draft, submit, approve, reject with comment, publish, withdraw) with DC1–DC6 rules; policy per DC4; MCP reads the effective revision.
- [ ] **DC-2 — File attachments.** Forecast 400-700.
  - Storage module (configurable directory, size and type checks by magic bytes, safe names, SHA-256, atomic write), upload on draft revisions, authorized download, immutability, cleanup of an unreferenced file when a draft is replaced or discarded; a way to discard a draft.
  - Also (DC-1 review): the workflow dates refuse a `datetime` like `next_review_date` does; a test proves the `IntegrityError` → `Conflict` message of revision writes.
- [ ] **DC-3 — Screens.** Forecast 700-1,200.
  - Document list (effective revisions, filters), detail with revision history, draft editor with attachment, workflow actions, withdrawal; role-based visibility.
  - Also (DC-1 review): the edit route of a withdrawn document refuses cleanly (tested); tests render the withdrawn and no-effective-revision branches of the detail page and the list's "De baja" badge.
- [ ] **DC-4 — Periodic review, docs and deployment notes.** Forecast 250-450.
  - Dashboard flag and notification for overdue reviews; README and `docs/mcp.md`; backup procedure for the storage directory.

## Checks

```bash
$ venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Baseline at `420f499`: 993 tests. Migrations are tested on PostgreSQL (`TEST_POSTGRES_URI`). File uploads are security-sensitive: expect high-risk reviews.

## Progress

| Task | Status | Commit | Checks | Review |
|---|---|---|---|---|
| DC-1 | Done | `19c1ec2` (`document_revisions`, migration `a7d3f5b9c2e4` converting each document into revision 1 in force, `app/services/document_revisions.py` with start_draft, edit_draft, submit, approve, reject, publish, withdraw; documents readable by every role, drafts by ADMIN and AUDITOR; no hard delete; MCP reads the revision in force and refuses document writes; minimal detail page) | RED: 58 tests (2 failures, 48 errors: required `issued_date`/`version`, `documents.content` NOT NULL, missing table and enum, delete still allowed). GREEN: 1,016 tests incl. PostgreSQL; removing the row lock makes the race test fail | Range `2a59ef3..19c1ec2`: **medium**, `slice_budget_reached` (2,044 lines; the user approved one PR above the 1,500 cap, 2026-10-10); consent granted; reliability review `review-d18afc86d8994c48` **approved** and acknowledged; its warning (edit route of a withdrawn document untested) and three suggestions moved into DC-2 and DC-3 |
| DC-2..DC-4 | Pending | — | — | — |

## Findings during implementation

- `issued_date`, `version`, `content`, `approved_by` and `signature` moved off `Document`: the content goes to the revisions, `issued_date` becomes revision 1's `effective_from`, and the old version, approver and signature texts are kept as `legacy_version`, `legacy_approved_by` and `legacy_signature`.
- Two partial unique indexes (with both `postgresql_where` and `sqlite_where`) enforce at most one revision in preparation and one in force per document.
- AUDITOR can reject and publish (updates); only ADMIN approves and withdraws; the approver can be neither the author nor the person linked to the acting user. `owner_id` is required when creating and cannot be cleared; converted documents may have no author.
- The `mcp` channel cannot create or update documents (`policy.can`), so `qms_modules` shows those permissions as false; MCP output carries `effective_revision` or null, never drafts.
- `docs/mcp.md` still describes documents as writable through MCP (fix in DC-4).

## Next step

DC-2.
