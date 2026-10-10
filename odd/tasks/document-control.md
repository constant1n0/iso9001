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
| RDD | On (global); assess every work-unit commit | `gentle-ai review mode status` |

## Tasks

Route for every task: **delegated direct**.

- [ ] **DC-1 — Revisions and workflow (service).** Forecast 600-1,000.
  - `DocumentRevision` model and migration (existing documents → revision 1 `vigente`, DC8); `Document` gains owner, next review date, withdrawal fields; workflow service (create draft, edit draft, submit, approve, reject with comment, publish, withdraw) with DC1–DC6 rules; policy per DC4; MCP reads the effective revision.
- [ ] **DC-2 — File attachments.** Forecast 400-700.
  - Storage module (configurable directory, size and type checks by magic bytes, safe names, SHA-256, atomic write), upload on draft revisions, authorized download, immutability, cleanup of an unreferenced file when a draft is replaced or discarded.
- [ ] **DC-3 — Screens.** Forecast 700-1,200.
  - Document list (effective revisions, filters), detail with revision history, draft editor with attachment, workflow actions, withdrawal; role-based visibility.
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
| DC-1..DC-4 | Pending | — | — | — |

## Next step

DC-1.
