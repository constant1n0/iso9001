# User guide

> **Translation.** This guide is the English translation of the
> [Guía de usuario](guia-de-usuario.md), which is the reference: changes are
> written in the Spanish guide first, and if the two differ, the Spanish guide
> applies.

This guide is for the people who use the ISO 9001 QMS in a web browser:
quality staff, managers and auditors. It explains each screen, what you can
do there with your role, and which rules the system enforces. It covers the
quality management system requirements of ISO 9001:2026 that the application
records.

The interface is in Spanish. This guide quotes its labels exactly, in bold,
with an English gloss the first time they appear: for example **No
conformidades** (nonconformities). A [glossary](#glossary) at the end lists the
most frequent terms.

Installation and server administration are in the
[operations guide](operaciones.md) (in Spanish).

## Contents

- [Signing in and your account](#signing-in-and-your-account)
- [Working with the screens](#working-with-the-screens)
- [Roles and access](#roles-and-access)
- [Dashboard](#dashboard)
- [Users](#users)
- [People and competence](#people-and-competence)
- [Nonconformities and corrective actions](#nonconformities-and-corrective-actions)
- [Document control](#document-control)
- [Audit programme, audits and findings](#audit-programme-audits-and-findings)
- [Risks and opportunities](#risks-and-opportunities)
- [Quality objectives](#quality-objectives)
- [Management review](#management-review)
- [Other registers](#other-registers)
- [Reports and e-mail notifications](#reports-and-e-mail-notifications)
- [Glossary](#glossary)

## Signing in and your account

### Signing in

1. Open the application's address. You see the **Acceso** (sign-in) page.
2. Type your **Usuario** (user name) and **Contraseña** (password) and press
   **Entrar** (sign in).
3. You land on the **Panel de control** (dashboard).

Wrong credentials, and accounts that an administrator has deactivated, get the
same message: **Credenciales inválidas**. After five attempts in one minute
from the same place, further attempts are refused for a while.

There is no public registration: an administrator creates your account (see
[Users](#users)). To leave, press **Salir** (sign out) in the top bar. The
session also ends by itself when you close the browser, after eight hours
without activity and, even while you keep working, twelve hours after you
signed in; the sign-in page says so with a message, and you just sign in
again. On a shared computer, press **Salir** or close the browser when you
finish.

### Forgotten password

1. On the sign-in page, follow **¿Has olvidado tu contraseña?** (forgotten
   your password?).
2. On **Recuperar contraseña** (recover password), type the e-mail address of
   your account and press **Enviar enlace** (send link).
3. The page always answers that an e-mail has been sent, whether or not the
   address belongs to an active account, so nobody can use it to discover
   accounts.
4. Open the link in the e-mail. It expires after one hour and stops working
   once the password changes.
5. On **Nueva contraseña** (new password), type the new password twice (at
   least 8 characters) and press **Guardar contraseña** (save password). Then
   sign in with it.

An administrator can also send you the link from the user list (see
[Users](#users)).

### Your profile

**Mi perfil** (my profile, `/perfil/`) is open to every user. Reach it from
the last item of the menu, or by clicking your user name in the top bar.

- **Datos de la cuenta** (account data): your user name, e-mail address, role
  and account state. Only an administrator changes your role.
- **Cambiar correo electrónico** (change e-mail): type the **Nuevo correo
  electrónico** and your **Contraseña actual** (current password), then press
  **Cambiar correo**. An address already used by another account is refused.
- **Cambiar contraseña** (change password): type the **Contraseña actual**,
  the **Nueva contraseña** (at least 8 characters, different from the current
  one) and **Confirmar nueva contraseña**, then press **Cambiar contraseña**.
- The two forms share a limit of ten attempts per hour for your account. A
  wrong current password shows **La contraseña actual no es correcta.**
- **Mis tokens de API** (my API tokens): the tokens issued to you for AI
  agents, with their **Nombre**, **Prefijo**, **Permisos** (scopes),
  **Estado** (**Activo**, **Caducado** expired, **Revocado** revoked),
  **Caduca** (expiry) and **Último uso** (last use). **Revocar** stops a token
  at once. The secret is never shown again after it is issued; tokens are
  issued from the server's command line (see
  [Agents and API tokens](#agents-and-api-tokens)).

## Working with the screens

### Navigation

The menu on the left groups the modules. Each entry shows the ISO 9001:2026
clause it supports. You only see the entries your role can read.

| Group | Entries |
|---|---|
| (top) | **Panel** (dashboard) |
| **Calidad** (quality) | **Riesgos** 6.1, **Oportunidades** 6.1, **Objetivos de calidad** 6.2, **Auditorías** 9.2, **Programas de auditoría** 9.2.2, **Revisión por la dirección** 9.3, **No conformidades** 10.2, **Mejoras** 10.3 |
| **Clientes** (customers) | **Satisfacción** 9.1.2, **Partes interesadas** 4.2 |
| **Personas y competencia** (people and competence) | **Personas** 7.2, **Competencias requeridas** 7.2, **Matriz de competencias** 7.2, **Capacitaciones** 7.2 |
| **Documentación** | **Documentos** 7.5 |
| **Administración** | **Usuarios** 5.3 |
| **Cuenta** (account) | **Mi perfil** |

On a narrow screen, the menu opens from the button at the left of the top bar.

### Conventions

- **Lists** have a **Filtros** (filters) panel: set the values and press
  **Buscar** (search); **Limpiar** (clear) removes them. A filter value that
  does not exist is ignored.
- **Buttons appear only when you may use them.** Your role and the record's
  state decide which buttons a page shows.
- **Confirmations.** Deleting, and most state changes, ask for confirmation
  first.
- **Messages.** After a save, a green message confirms it. When the system
  refuses an action, a red message explains why (the texts are quoted in this
  guide), and the form comes back with what you typed. Opening a page your role
  cannot use shows **No tienes permiso para acceder a esta página.** and
  returns you to the dashboard.
- **Dates** are shown as day/month/year. "Today" is the date in the
  organization's time zone, set on the server.
- **People pickers.** Many forms choose a person from **Personas** (see
  [People and competence](#people-and-competence)). Only active people are
  offered; a record keeps a person who is deactivated later.
- **Your own person.** When your user account is linked to a person, some
  forms start on that person (the author of a new document, the recorder of a
  measurement, the approver of an audit programme), and the system records it
  when you hold a management review or withdraw a document. Ask an
  administrator or auditor to link your account to your person.
- **Legacy names.** Older records of training, nonconformities and audits
  hold a free-text name (**Personal**, **Responsable**, **Auditor**) next to
  the person picker. Existing texts are kept and never matched to a person. If
  you leave the text blank and pick a person, the text takes that person's
  name. The text is required only when no person is picked.
- **PDFs** open in a new browser tab.

## Roles and access

Every account has one of three roles: **Administrador** (administrator),
**Auditor** or **Operativo** (operational staff). The role decides what you
can read, create, edit, delete and approve in each module.

| Module | Administrador | Auditor | Operativo |
|---|---|---|---|
| Users | Read, create, edit, deactivate, reactivate, send reset links | Read | No access |
| Your own profile | Everything | Everything | Everything |
| People | Read, create, edit, delete | Read, create, edit | Read |
| Competence (requirements, records, matrix) | Read, create, edit, delete | Read, create, edit | Read |
| Training | Read, create, edit, delete | Read, create, edit | Read, create, edit |
| Nonconformities | Read, create, edit, delete; cancel, close, reopen | Read, create, edit; cancel, close | Read, create, edit |
| Corrective actions | Read, create, edit, delete; verify | Read, create, edit; verify | Read, create, edit |
| Documents | Read every revision, create, edit, submit, reject, publish, attach files; approve, withdraw | Read every revision, create, edit, submit, reject, publish, attach files | Read the revision in force |
| Audit programmes, audits and findings | Read, create, edit, delete; approve programmes | Read, create, edit | No access |
| Risks, opportunities and their actions | Read, create, edit, delete; evaluate the actions' effectiveness | Read, create, edit; evaluate the actions' effectiveness | Read |
| Quality objectives and measurements | Read, create, edit, change state, delete | Read, create, edit, change state | Read |
| Management reviews and their actions | Read, create, edit, delete; hold, close | Read, create, edit | No access |
| Improvements, customer satisfaction, interested parties | Read, create, edit, delete | Read, create, edit | Read, create, edit |
| Roles, training resources, processes, audit indicators (no screens) | Read, create, edit, delete | Read, create, edit | Read |

- Nobody deletes users or documents: users are deactivated and documents are
  withdrawn, so the history stays complete.
- Other rules depend on the record's state; each module section below lists
  them.
- Every change is written to the audit log with the user who made it.

### Agents and API tokens

AI agents (for example Claude Code or Codex) work with the QMS through its MCP
server. An administrator issues each agent an API token from the server's
command line (see [API tokens](operaciones.md#tokens-de-api) in the operations
guide, in Spanish).

- A token acts as the user it was issued to, with that user's role. Its
  scopes can only reduce the role's rights: a `read` token only reads, and a
  `write` token never reaches a module the user's role cannot write.
- An agent never deletes, never writes documents and never manages tokens.
  Verifying corrective actions, closing, cancelling and reopening
  nonconformities, approving audit programmes, holding and closing management
  reviews, and the PDFs are done in the web interface only.
- What an agent saves carries its user's name in the audit log, like a change
  made on the web.
- You see and revoke your own tokens on **Mi perfil**; deactivating a user
  revokes their active tokens.

The modules, fields and client set-up are in the [MCP server guide](mcp.md)
(in Spanish).

## Dashboard

The **Panel de control** (`/dashboard/`) opens after you sign in. It shows the
state of the QMS for every role. The **Auditorías** figure and the **Auditorías
próximas** card are shown only to the roles that may read audits
(administrators and auditors); an operativo sees no audit figure, area or
date.

**Shortcut buttons** at the top create records: **+ Auditoría** (administrators
and auditors), **+ No conformidad**, **+ Capacitación** (training) and
**+ Encuesta** (customer survey).

**Figures:**

| Figure | What it counts |
|---|---|
| **Auditorías** | Every audit recorded, in any state; below, how many are still to be held in the next 7 days |
| **No conformidades abiertas** | Open nonconformities (not closed or cancelled) out of the total; below, the closed ones |
| **Satisfacción media** | The average score of every customer survey, out of 10 |
| **Capacitaciones** | Every training session recorded; below, how many are dated in the next 30 days |

**Alert cards:**

- **Auditorías próximas** (upcoming audits), for administrators and auditors:
  audits still to be held (neither completed nor cancelled) dated today or in
  the next 7 days, soonest first, with their area and date.
- **No conformidades abiertas**: the open nonconformities, oldest detection
  first, with their responsible person or **Sin asignar** (unassigned).
- **Capacitaciones próximas**: training sessions dated in the next 30 days.
- **Revisión de documentos** (document review): documents in use whose
  periodic review is overdue or due within 30 days, with a **Vencida**
  (overdue) badge on the overdue ones. An operativo sees only documents in
  force. When some are overdue, a link opens the document list filtered on
  them.
- **Objetivos de calidad**: active quality objectives that miss their target
  (**No cumple la meta**) or have an overdue measurement (**Medición
  vencida**). When there are some, a link lists the active objectives.
- **Riesgos y oportunidades** (risks and opportunities): how many open
  records need attention, each count linking to the list filtered on them:
  **Riesgos altos o críticos sin acción abierta** (high or critical risks
  without a pending or in-progress action), **Riesgos con la revisión
  vencida**, **Oportunidades con la revisión vencida** (overdue reviews),
  **Riesgos sin valorar** and **Oportunidades sin valorar** (not assessed).
  Only non-zero counts appear; without any, the card shows **Ningún riesgo ni
  oportunidad requiere atención.**
- **Acciones de la revisión por la dirección** (management review actions),
  for administrators and auditors: the overdue actions with their owner and
  deadline. When there are some, a link opens the action register filtered on
  them.

**Charts:**

- **No conformidades**: open against closed nonconformities.
- **Satisfacción del cliente**: the average survey score of each month, over
  the last 12 months.

## Users

**Usuarios** (`/usuarios/`, menu **Administración**) manages the accounts
that sign in (ISO 9001:2026 clause 5.3). Administrators manage them; auditors
see the list read-only; operativo users cannot open it.

The list shows each account's **Usuario**, **Correo electrónico** (e-mail),
**Rol** and **Estado** (**Activo** or **Inactivo**).

**To create a user**, press **+ Nuevo usuario** and fill in:

- **Nombre de usuario**: 4 to 150 characters, unique, and it never changes
  afterwards;
- **Correo electrónico**: unique, compared without regard to case;
- **Rol**: **Administrador**, **Auditor** or **Operativo** (the default);
- **Contraseña** and **Confirmar contraseña**: at least 8 characters.

Press **Crear usuario**.

**To change an e-mail or a role**, press **Editar** on the list.

**Account actions** (on the list and on the edit page):

- **Enviar enlace de contraseña** (send password link) e-mails the user a
  one-hour reset link. It needs an active account with an e-mail address. Each
  administrator sends at most ten links an hour, from any network address.
- **Desactivar** (deactivate): the user can no longer sign in, loses any open
  session and has their active API tokens revoked. The audit trail keeps their
  name.
- **Reactivar** (reactivate) gives access back.

**Refusals:**

- **No puedes cambiar tu propio rol.** and **No puedes desactivar tu propia
  cuenta.**
- **No se puede desactivar ni retirar el rol al último administrador activo.**
  At least one active administrator always remains.
- **Ya existe un usuario con ese nombre de usuario.** and **Ya existe un
  usuario con ese correo electrónico.**

Account changes are also written to the security log.

## People and competence

The **Personas y competencia** group records who works under the QMS, which
roles they hold, the competence each role requires and the competence each
person has demonstrated (ISO 9001:2026 clauses 5.3 and 7.2). Every role reads
people and competence; administrators and auditors create and edit; only
administrators delete. **Capacitaciones** (training), in the same menu group,
is described under [Other registers](#other-registers).

### People

**Personas** (`/personas/`) are the people who do work under the QMS. They
are separate from user accounts: a person may have no account.

- **Fields**: **Nombre completo** (full name), **Correo electrónico**
  (optional, unique among people), **Roles** (any number of QMS roles),
  **Usuario vinculado** (linked user account, at most one person per user),
  **Activa** (active) and **Notas**.
- **List filters**: **Nombre**, **Estado** (**Todas**, **Activas**,
  **Inactivas**) and **Rol**.
- **A person's page** shows the data, the roles and the **Competencias
  acreditadas** (demonstrated competence).
- **Deleting**: a person that other records cite (a nonconformity, an action,
  an audit, a document, a review and so on) cannot be deleted: **No se puede
  eliminar una persona citada en otros registros; desactívala en su lugar.**
  Untick **Activa** instead.

The QMS roles themselves (**Roles y responsabilidades**) have no screen of
their own: see [Other registers](#other-registers).

### Required competence

**Competencias requeridas** (`/competencias/requisitos/`) lists what each role
requires. To add one, press **+ Nuevo requisito** and fill in:

- **Rol**;
- **Tipo**: **Educación** (education), **Formación** (training),
  **Habilidad** (skill) or **Experiencia** (experience);
- **Descripción**;
- **Criterio para evidenciarla** (how it is evidenced).

The list filters by role. A requirement cited by demonstrated competence
cannot be deleted.

### Demonstrated competence

On a person's page, **+ Añadir competencia** records evidence:

- **Requisito de competencia** (optional; only records tied to a requirement
  count in the matrix);
- **Evidencia** (text or a reference);
- **Capacitación** (optional training session as evidence);
- **Fecha de obtención** (date obtained) and **Fecha de caducidad** (expiry,
  optional);
- **Evaluación de la eficacia** (effectiveness evaluation): **Pendiente**,
  **Eficaz** or **No eficaz**;
- **Fecha de evaluación** and **Evaluador** (evaluating person).

**Rules:**

- The expiry date cannot be before the date obtained.
- An evaluation other than pending needs the evaluation date and the
  evaluator: **Una evaluación de eficacia distinta de «pendiente» necesita la
  fecha de evaluación y el evaluador.**
- The evaluation date cannot be before the date obtained.

### Competence matrix

**Matriz de competencias** (`/competencias/matriz`) shows, for every role with
requirements, its active people (rows) against each requirement (columns),
judged on today's date. You can filter by **Rol**. A record without an expiry
date never expires, and one expiring today is still valid. Each cell links to
the person's page and shows one status:

| Status | Meaning |
|---|---|
| **Cumplida** (met) | An unexpired record evaluated effective, even if a newer attempt was not |
| **Pendiente de evaluación** | Otherwise, the newest unexpired record is still pending |
| **No eficaz** | Otherwise, the newest unexpired record was not effective |
| **Caducada** (expired) | Every record has expired |
| **Falta** (missing) | There is no record |

## Nonconformities and corrective actions

**No conformidades** (`/no_conformidades/`, ISO 9001:2026 clause 10.2) records
what went wrong, how it was contained, its root cause, the corrective actions
taken and whether they worked. Every role records nonconformities and
corrective actions.

### Screens

- **The list** filters by **Descripción**, **Estado**, **Origen**, **Gravedad**
  and **Detectada el** (detection date). Each row has **Ver** (view),
  **Editar** (while open), **PDF** and, for administrators, **Eliminar**.
- **+ Nueva no conformidad** opens the form; **Registrar no conformidad**
  saves it.
- **A nonconformity's page** (**No conformidad n.º** *N*) shows its
  **Estado** (state) with the closing button, its **Datos** (data), the
  **Acciones correctivas** table and, at the bottom, the cancellation form.

### Fields

- **Descripción** and **Fecha Detectada** (detection date), both required;
- **Origen** (origin): **Auditoría**, **Cliente**, **Proceso**, **Proveedor**
  (supplier) or **Otro**;
- **Gravedad** (severity): **Mayor** (major), **Menor** (minor) or
  **Observación**;
- **Responsable** (legacy free text) and **Responsable (persona)**;
- **Contención** (containment) and **Causa raíz** (root cause);
- **Acción Correctiva**: a free-text field kept from earlier versions. The
  structured corrective actions below replace it; the page shows its text as
  **Acción correctiva (texto anterior)** when there is one.

### States

A nonconformity moves through **Abierta** (open) → **Acción planificada**
(action planned) → **En verificación** (under verification) → **Cerrada**
(closed), or ends **Cancelada** (cancelled). Nobody chooses the state in the
form:

- while open, it follows its corrective actions:
  - without actions it is **Abierta**;
  - the first action moves it to **Acción planificada**, where it stays while
    an action is not done;
  - once every action is done, it moves to **En verificación**;
  - when the latest action is verified not effective, it returns to **Acción
    planificada**: register a new action;
- closing, cancelling and reopening are buttons, described below.

### Corrective actions

On the nonconformity's page, **+ Añadir acción** registers an action:

- **Descripción**;
- **Responsable** (owner, a person);
- **Fecha prevista** (planned date);
- **Fecha de realización** (done date), once it is done.

The action's status is derived: **Planificada** (planned), **Realizada**
(done), **Verificada eficaz** (verified effective) or **Verificada no eficaz**
(verified not effective).

- The done date cannot be before the detection date, and the detection date
  cannot be moved after the done date of an action.
- A verified action can no longer be edited: **Una acción correctiva
  verificada no se puede modificar.** An administrator can still delete it
  while the nonconformity is open.

### Verifying effectiveness

Administrators and auditors press **Verificar** (verify) on a done action and
record:

- **Resultado**: **Eficaz** or **No eficaz**;
- **Fecha de verificación**: not before the done date;
- **Verificada por** (verifier): never the action's owner, who is left out of
  the picker;
- **Evidencia**.

Press **Registrar verificación**. Only a done action can be verified: **Solo se
puede verificar una acción correctiva realizada; indica antes su fecha de
realización.**

### Closing, cancelling and reopening

- **To close**, an administrator or auditor presses **Cerrar no conformidad**.
  It needs at least one action, every action done and verified, and the latest
  action verified effective. An earlier ineffective action stays as evidence
  and does not block closing once a later action proved effective. While
  something is missing, the page lists it under **No se puede cerrar
  todavía:** (for example *1 acción pendiente de verificar*, or *la última
  acción correctiva no fue eficaz; registra una nueva acción correctiva*).
- **To cancel**, an administrator or auditor types the **Motivo de la
  cancelación** (reason) at the bottom of the page and presses **Cancelar no
  conformidad**. The reason is required.
- A closed or cancelled nonconformity, and its actions, are read-only: **Esta
  no conformidad está cerrada o cancelada y no se puede modificar; un
  administrador puede reabrirla.**
- **To reopen**, an administrator presses **Reabrir**. The nonconformity
  returns to the state its actions call for, and loses the closing date and
  the cancellation reason.
- **Deleting** a nonconformity (administrators) also deletes its corrective
  actions. An audit finding that opened it keeps existing, without the link.

### PDF

**PDF** produces the **Informe de no conformidad**: its fields, its state and
the closing or cancellation date (with the reason), and a table of its
corrective actions with their dates, status and verification (result, date,
verifier and evidence).

## Document control

**Documentos** (`/documents/`, ISO 9001:2026 clause 7.5) controls the
documented information of the QMS. A document keeps its identity and owner;
its text lives in numbered revisions (1, 2, 3…) that are reviewed and approved
before they come into force.

### Who sees what

- Every role reads the revision in force (**Vigente**) of each document.
- Drafts, revisions in review and approved revisions not yet published are
  visible only to administrators and auditors. An operativo does not see
  documents that have no revision in force.
- Administrators and auditors create documents, write drafts, submit, reject,
  publish and attach files. Only administrators approve and withdraw.

### Screens

- **The list** filters by **Categoría**, **Propietario** (owner), **Estado**
  (**Vigente** in force, **Sin publicar** never published, **De baja**
  withdrawn) and **Solo con la revisión periódica vencida** (overdue review
  only). It shows the **Código**, **Título**, **Categoría**, **Propietario**,
  **Revisión vigente**, **Próxima revisión** with a **Vencida** badge, and
  **Estado**.
- **A document's page** shows its **Datos**, the revision in force with its
  text and file, the revision in preparation (for administrators and
  auditors) and the **Historial de revisiones** (revision history): number,
  state, author, approver, approval date, in force from, obsolete from, change
  summary, last rejection and file.

### Creating a document

Press **+ Nuevo documento** and fill in:

- **Título** and **Código de Identificación** (code, unique);
- **Categoría**: **Manual de Calidad**, **Procedimiento Operativo**,
  **Instrucción de Trabajo**, **Plan de Acción Correctiva**, **Plan de Acción
  Preventiva**, **Registro de Calidad**, **Informe de Revisión por la
  Dirección**, **Política de Seguridad y Salud Ocupacional**, **Indicador de
  Desempeño**, **Plan de Capacitación** or **Otro**;
- **Propietario** (owner, a person, required);
- **Próxima revisión** (next periodic review date, optional);
- **Autor de la revisión 1** and **Contenido** (the text of revision 1).

The document starts with revision 1 in **Borrador** (draft).

**Editar datos** changes the document's own fields later. The code can change
only until a revision has been published; after that it is shown read-only
and a change is refused.

### Revision workflow

| State | Meaning | Next step |
|---|---|---|
| **Borrador** | Draft being written | **Enviar a revisión** (submit for review) |
| **En revisión** | Waiting for approval | **Aprobar** (approve) or **Rechazar** (reject, back to draft) |
| **Aprobado** | Approved, not yet in force | **Publicar** (publish) |
| **Vigente** | In force | Replaced by the next published revision |
| **Obsoleto** | Replaced or withdrawn; kept as evidence | None |

1. **Write.** **Editar borrador** (edit draft) changes the **Contenido**, the
   **Resumen de cambios** (change summary) and the **Autor de la revisión**.
2. **Submit.** **Enviar a revisión** needs the change summary: **El resumen de
   cambios es obligatorio para enviar la revisión a revisión.**
3. **Approve or reject.**
   - An administrator presses **Aprobar** and picks the person who approves in
     **Aprobada por**. The approver can never be the revision's author: the
     author may be neither the person named nor the person linked to the
     approving user (**El autor de una revisión no puede aprobarla.**).
   - **Rechazar** returns the revision to draft with a **Motivo del rechazo**
     (reason), shown as **Último rechazo** in the history.
4. **Publish.** **Publicar** puts an approved revision in force from today;
   the revision it replaces becomes **Obsoleto** the same day. Revisions in
   force or obsolete never change again.
5. **Next revision.** On a document in force with nothing in preparation,
   **+ Nueva revisión** starts a draft from the text in force; give its
   **Autor de la revisión** and, optionally, a change summary. A document has
   at most one revision in preparation (**El documento ya tiene una revisión
   en preparación.**) and at most one in force.
6. **Discard.** **Descartar borrador** deletes a later draft and its file.
   Revision 1 of a document that has never been in force cannot be discarded:
   withdraw the document instead.

### Attachments

Each revision may carry one file: PDF, DOCX, XLSX or ODT.

- **To attach** while the revision is a draft, choose the file under the
  revision in preparation and press **Adjuntar** (attach). A new file replaces
  the previous one; **Quitar fichero** (remove file) removes it.
- The page shows the size limit (20 MB unless the server sets another).
- The file type is checked by its content as well as its extension:
  - **Solo se admiten ficheros PDF, DOCX, XLSX u ODT.**;
  - **La extensión del fichero no corresponde a su contenido.**;
  - **El fichero está vacío.**
- Once a revision is in force, it and its file never change.
- **Downloading:** every role downloads the file of the revision in force;
  only administrators and auditors download the files of other revisions. The
  file always downloads; the browser does not open it.

How the server stores and backs up the files is in the
[operations guide](operaciones.md#ficheros-adjuntos-de-los-documentos) (in Spanish).

### Withdrawal

Documents are never deleted. An administrator presses **Dar de baja**
(withdraw) and types the **Motivo de la baja** (reason):

- the revision in force becomes obsolete;
- the date, the reason and the administrator's person are recorded;
- the document becomes read-only (**El documento está dado de baja y no se
  puede modificar.**) and shows **De baja**.

### Periodic review

A document in use whose **Próxima revisión** date has passed is flagged
**Vencida** on the list and on its page. The dashboard card **Revisión de
documentos** lists the reviews overdue or due within 30 days, and a weekly
e-mail reminds owners and administrators (see
[E-mail notifications](#e-mail-notifications)). Updating the next review date
with **Editar datos** clears the flag.

## Audit programme, audits and findings

Internal audits (ISO 9001:2026 clause 9.2) are planned in a yearly programme,
carried out by an independent team and documented with classified findings.
Nonconformity findings feed the corrective-action loop. Administrators and
auditors use these screens; operativo users have no access.

### Audit programme

**Programas de auditoría** (`/programas-auditoria/`, clause 9.2.2) holds one
programme per year.

- **To create one**, press **+ Nuevo programa** and fill in **Año** (year,
  2000 to 2100, one programme per year), **Objetivos** and **Alcance**
  (scope). It starts as **Borrador**.
- **To approve it**, an administrator presses **Aprobar** on its page and
  picks **Aprobado por** (approver). The picker starts on the administrator's
  own person, and the approval date is today. The programme becomes
  **Aprobado**.
- The year of an approved programme never changes, nor does a draft's once it
  has audits.
- **The programme's page** shows its state and approval, its objectives and
  scope, and **Auditorías del programa**: each audit with its date, area, lead
  auditor, state and its findings counted by type (up to the first 100 audits).
  **+ Nueva auditoría** there creates an audit already in the programme.
- A programme with audits cannot be deleted: **No se puede eliminar un
  programa que tiene auditorías; quítalas antes.**
- **PDF** produces the programme: year, state, approver and approval date,
  objectives and scope, and every audit by date with its area, lead auditor,
  state and findings by type.

### Audits

**Auditorías** (`/auditorias/`) lists the audits ten per page and filters by
**Área auditada**, **Auditor**, **Estado**, **Desde** and **Hasta** (date
range). Click an audit's area to open its page.

**To plan an audit**, press **+ Nueva auditoría** and fill in:

- **Área Auditada** and **Fecha**;
- **Programa de auditoría** (optional): the audit's date must fall in the
  programme's year;
- **Auditor** (legacy text) and **Auditor (persona)**: the lead auditor;
- **Equipo auditor** (audit team, several people);
- **Responsable del área auditada** (audited-area owner, a person);
- **Alcance** and **Criterios de auditoría** (for example *ISO 9001:2026,
  procedimientos internos*);
- **Resultado** (required) and **Acción Correctiva** (free text);
- **Estado**: **Pendiente**, **En Proceso**, **Completada** or **Cancelada**.

**Independence.** Neither the lead auditor nor any team member may own the
audited area. The form comes back with the reason: **El auditor jefe no puede
ser el responsable del área auditada.** or **Ningún miembro del equipo auditor
puede ser el responsable del área auditada.**

**The audit's page** (**Auditoría n.º** *N*) shows the planning, scope and
criteria, the result and the **Hallazgos** (findings).

**Deleting** an audit (administrators) is refused while it has findings:
**No se puede eliminar una auditoría que tiene hallazgos; elimina antes sus
hallazgos.**

**PDF** produces the **Informe de auditoría**: programme, area, area owner,
lead auditor, team, date, state, scope, criteria, result and corrective
action, and a table of findings (type, clause, description, evidence and
nonconformity number).

### Findings

On the audit's page, **+ Añadir hallazgo** records a finding:

- **Tipo**: **No conformidad mayor**, **No conformidad menor**,
  **Observación** or **Oportunidad de mejora**;
- **Cláusula de la norma** (optional): dotted numbers from 4 to 10, such as
  `8.5.1`;
- **Descripción** (required) and **Evidencia**.

**Rules:**

- A finding never moves to another audit.
- A completed audit still takes findings. A cancelled audit's findings are
  read-only: **No se pueden añadir ni modificar hallazgos de una auditoría
  cancelada.**

### Nonconformities opened by findings

A **No conformidad mayor** or **No conformidad menor** finding opens its
nonconformity in the same save, and the confirmation names it (**Se ha abierto
la no conformidad n.º** *N*). The nonconformity takes:

- origin **Auditoría** and severity **Mayor** or **Menor**;
- the audit's date as its detection date;
- the description *Hallazgo de auditoría (area, date): finding description*;
- the audited-area owner as responsible, when that person is active.

The finding links to the nonconformity, which then follows the
[corrective-action loop](#nonconformities-and-corrective-actions).
Afterwards:

- changing the finding between *mayor* and *menor* changes the
  nonconformity's severity only while it is **Abierta**; otherwise the change
  is refused;
- changing the finding to another type keeps the nonconformity and the link;
  changing it back reuses that link, or opens a new nonconformity if the
  earlier one was deleted;
- other edits never touch the nonconformity, which is a record of its own;
- deleting a finding (administrators) never deletes its nonconformity, and
  deleting the nonconformity clears the finding's link.

## Risks and opportunities

**Riesgos** (risks, `/riesgos/`) and **Oportunidades** (opportunities,
`/oportunidades/`), in the **Calidad** menu, are the two registers of
ISO 9001:2026 clause 6.1, which treats risks and opportunities as separate
concepts. A risk is assessed by its likelihood and impact; an opportunity by
its benefit and feasibility. Each record has its actions, with an owner, a
deadline and an effectiveness evaluation, and a risk also has a residual
assessment. Every role reads both registers and their PDFs. Administrators
and auditors create and edit records, and add, edit and evaluate actions;
only administrators delete.

### The record

Press **+ Nuevo riesgo** or **+ Nueva oportunidad** and fill in:

- **Título** and **Descripción**;
- **Origen**: **Interno** (internal) or **Externo** (external) to the
  organization;
- **Parte interesada** (interested party, optional; from **Partes
  interesadas**) and **Proceso** (optional; see
  [Other registers](#other-registers));
- **Responsable** (owner, a person);
- the assessment, the risk's **Tratamiento** (treatment) or the opportunity's
  **Decisión** (see [Assessment](#assessment)) and the **Estado** (see
  [States and review](#states-and-review));
- **Fecha de identificación** (identification date): the form starts on
  today; never in the future;
- **Próxima revisión** (next review, optional): never before the
  identification date;
- **Notas** (notes).

A record with actions cannot be deleted: **No se puede eliminar un riesgo que
tiene acciones; elimina antes sus acciones.** (or the same message for an
opportunity).

### Assessment

Each value runs from 1 to 5. The product of a pair gives the risk's level or
the opportunity's priority:

| Register | Values | Result |
|---|---|---|
| Risk | **Probabilidad (1-5)** (likelihood) × **Impacto (1-5)** (impact) | **Nivel** (level): **Bajo** (low) 1–4, **Medio** (medium) 5–9, **Alto** (high) 10–16, **Crítico** (critical) 20–25 |
| Opportunity | **Beneficio (1-5)** (benefit) × **Viabilidad (1-5)** (feasibility) | **Prioridad** (priority): **Baja** (low) 1–6, **Media** (medium) 8–12, **Alta** (high) 15–25 |

- Both values of a pair are given together, or both are left on **Sin
  valorar** (not assessed). Until then the record shows **Sin valorar**: a
  to-do the dashboard flags.
- A risk's **Tratamiento**: **Aceptar** (accept), **Mitigar** (mitigate),
  **Transferir** (transfer) or **Evitar** (avoid). An opportunity's
  **Decisión**: **Aprovechar** (pursue), **Posponer** (postpone) or
  **Descartar** (discard). Both may stay on **— Sin decidir —** (undecided).
- A risk's **residual assessment**: **Probabilidad residual (tras las
  acciones)** and **Impacto residual (tras las acciones)** (after the actions)
  give the **Nivel residual** (residual level) on the same bands. It needs the
  risk's assessment first: **La valoración residual necesita antes la
  valoración del riesgo («probabilidad» e «impacto»).**

### States and review

| Register | Open states | Closed states |
|---|---|---|
| Risk | **Identificado** (identified), **En tratamiento** (being treated), **Controlado** (under control) | **Cerrado** (closed) |
| Opportunity | **Identificada** (identified), **En curso** (in progress) | **Aprovechada** (pursued), **Descartada** (discarded) |

- The state is changed on the record's form.
- A closed record takes no new actions: **El riesgo está cerrado: no admite
  acciones nuevas.** or **La oportunidad está aprovechada o descartada: no
  admite acciones nuevas.** Its existing actions can still be edited and
  evaluated.
- **Overdue rule.** An open record whose **Próxima revisión** is before today
  shows **Revisión vencida** (review overdue). A closed record is never
  overdue.

### Actions

On the record's page, **+ Nueva acción** records:

- **Descripción**;
- **Responsable** (owner, a person);
- **Fecha límite** (deadline);
- **Estado**: **Pendiente** (pending, to start with), **En curso** (in
  progress), **Completada** (completed) or **Cancelada** (cancelled);
- **Fecha de finalización (solo si está completada)** (completion date, only
  when completed): required on a completed action, never in the future and
  only in that state.

A pending or in-progress action past its deadline shows **Vencida**
(overdue). An action never moves to another record.

**Effectiveness evaluation (6.1.2 b).** A completed action shows **Evaluar
eficacia** (evaluate effectiveness, or **Reevaluar** once evaluated), which
records:

- **Resultado** (result): **Eficaz** (effective) or **No eficaz** (not
  effective);
- **Fecha de la evaluación** (evaluation date): the form starts on today;
  never before the action's completion nor in the future;
- **Comentario**.

Only a completed action is evaluated: **Solo se evalúa la eficacia de una
acción completada.**

### Screens

- **The list** filters by **Estado**, **Nivel** (or **Prioridad**),
  **Responsable**, **Proceso**, **Origen**, **Sin valorar**, **Revisión
  vencida** and **Abierto** (or **Abierta**, open); risks also by
  **Probabilidad**, **Impacto** and **Alto o crítico sin acción abierta**
  (high or critical without an open action). It shows each record's process,
  owner, level and residual level with the treatment (or priority with the
  decision), state, **Próxima revisión** (with an overdue badge) and
  **Acciones abiertas** (pending or in-progress actions).
- **The heat map** (**Mapa de calor**), above the risk list, is a 5 × 5 grid:
  likelihood on the rows (5 at the top) and impact on the columns. Each cell
  counts the open assessed risks with that likelihood and impact, takes the
  colour of its level and opens the list filtered by it.
- **A record's page** shows its **Valoración** (assessment, with the level
  before and after the actions for a risk), its **Identificación**, its
  **Descripción**, its **Notas** and the **Acciones** table.

### PDF

**PDF** on each list produces the whole register as of today:

- a summary of the open records: how many there are by level (or priority),
  how many are **Sin valorar** and how many have a **Revisión vencida**, and
  how many are closed;
- in the risk PDF, the heat map as a table;
- the open records and, in a section of their own, the closed ones, each
  group by level (or priority) from the highest, the unassessed last, then by
  title. For each record: process, owner, origin, level and residual level
  with the treatment (or priority and decision), state, next review and
  description;
- a table of its actions: description, owner, deadline, state and
  effectiveness (result, date and comment).

Risks by level and opportunities by priority are also management review
inputs (see [Inputs (9.3.2)](#inputs-932)).

## Quality objectives

**Objetivos de calidad** (`/objetivos-calidad/`, ISO 9001:2026 clause 6.2)
records what the QMS sets out to achieve, how it is measured and whether it is
on track. Every role reads objectives and measurements, because objectives
must be communicated. Administrators and auditors create, edit, record
measurements and change an objective's state; only administrators delete.

### The objective

Press **+ Nuevo objetivo** and fill in:

- **Título** and **Descripción**;
- **Proceso** (optional; processes come from the process register, see
  [Other registers](#other-registers));
- **Responsable** (owner, a person);
- **Indicador** and **Unidad** (for example %, days);
- **Meta** (target) and **Sentido de la meta** (direction): **≥ igual o
  superior a la meta** is met at or above the target, **≤ igual o inferior a
  la meta** at or below it;
- **Valor inicial** (baseline, optional);
- **Frecuencia de medición**: **Mensual**, **Trimestral** (quarterly),
  **Semestral** (half-yearly) or **Anual**;
- **Fecha de inicio** and **Fecha límite** (deadline, not before the start);
- **Año** (year; left empty, it takes the start date's year);
- **Recursos necesarios** (resources needed) and **Cómo se evalúan los
  resultados** (how results are evaluated).

Numbers take a decimal point or comma.

### States

An objective starts **Activo**. Its page closes it with **Alcanzado**
(achieved), **No alcanzado** (not achieved) or **Cancelar** (state
**Cancelado**); **Reactivar** makes it active again. Only an active objective
takes measurements.

### Measurements

On the objective's page, **+ Nueva medición** records:

- **Fecha**: the form starts on today;
- **Valor**;
- **Registrada por**: the form starts on your own person;
- **Comentario**.

**Rules:**

- One measurement per objective and date: **El objetivo ya tiene una medición
  en esa fecha.**
- The date lies in the objective's period and is never in the future.
- Measurements are added, edited and deleted only while the objective is
  **Activo**: **Solo se pueden añadir o modificar mediciones de un objetivo
  activo.**
- Once an objective has measurements, its target and direction are fixed and
  it cannot be deleted (delete its measurements first, while it is active).
  Its period must keep including them.

### Status and trend

- Each measurement meets the target or not, and so does the objective,
  through its latest measurement: **Cumple**, **No cumple**, or **Sin medir**
  without measurements.
- **Tendencia** (trend) compares the latest measurement with the one before:
  **Sube** (up), **Baja** (down) or **Estable**.
- **Overdue rule.** An active objective's next measurement is due one
  frequency period after its latest measurement, or after its start date when
  it has none, and never later than its deadline. Once that date has passed,
  the objective shows **Medición vencida** (overdue measurement).

### Screens

- **The list** filters by **Año**, **Estado**, **Responsable** and
  **Proceso**. It shows each objective's indicator, target, latest value,
  **Meta cumplida** (target met), owner, deadline, **Próxima medición** (next
  measurement, with an overdue badge) and state.
- **An objective's page** shows its **Resultado** (target, baseline, latest
  value and whether it meets the target, trend, next measurement), its
  **Planificación**, the **Mediciones** table and the **Evolución** chart of
  the measurements against the target.

### PDF

**PDF** *year* on the list produces the objectives of the year in the
**Año** filter, or of the current year:

- the objectives counted by state, how many active objectives meet their
  target and how many have an overdue measurement;
- each objective, by deadline and title, with its process, indicator, target,
  baseline, owner, deadline, frequency, state, latest value and date, met or
  not, trend, next measurement, resources and evaluation;
- a table of its measurements: date, value, met or not, and comment.

## Management review

**Revisión por la dirección** (`/revisiones-direccion/`, ISO 9001:2026 clause
9.3) records each review of the QMS by top management: what was presented,
what was concluded and what was decided. Administrators and auditors read and
write reviews and their actions; only administrators hold, close and delete.
Operativo users have no access.

### The review

Press **+ Nueva revisión** and fill in:

- **Fecha de la reunión** (meeting date);
- **Periodo revisado: desde** and **hasta** (period reviewed): it ends on or
  before the meeting (**El periodo revisado debe terminar en la fecha de la
  reunión o antes.**);
- **Asistentes** (attendees, people);
- a comment for each input of clause 9.3.2, a) to f) (the c) comment also
  covers external providers).

### States

| State | What can change | Who moves it |
|---|---|---|
| **Borrador** (draft) | The meeting, period, attendees and comments: **Editar** | An administrator presses **Celebrar revisión** (hold) |
| **Celebrada** (held) | Only the comments and the conclusions: **Comentarios y conclusiones** | An administrator presses **Cerrar revisión** (close) |
| **Cerrada** (closed) | Nothing; only the follow-up of its actions | Final: there is no way back |

- **Holding** needs a meeting date that is not in the future and at least one
  attendee (**Indica al menos un asistente antes de celebrar la revisión.**).
  It records the administrator's person as **Celebrada por** and freezes the
  inputs.
- **Conclusions.** Once the review is held, **Comentarios y conclusiones**
  takes the comments and the **Conclusiones sobre la conveniencia, adecuación y
  eficacia del SGC** (conclusions on the suitability, adequacy and
  effectiveness of the QMS).
- **Closing** needs the conclusions (**Escribe las conclusiones antes de
  cerrar la revisión.**) and records the closing date.
- **Deleting** (administrators) is only for a draft without actions.

### Inputs (9.3.2)

The review's page lists each input with figures drawn from the registers for
the period, next to its comment:

- **a)** the actions of earlier reviews: open at the end of the period, by
  state, completed after it, overdue, and completed in the period;
- **b)** interested parties, and risks and opportunities: registered, new and
  changed in the period, the risks by level with the open high or critical
  ones, and the opportunities by priority;
- **c)** customer satisfaction (surveys and average score), quality objectives
  (on target, off target, without measurement, with an overdue measurement,
  by state), process performance (audit indicator records), nonconformities
  (detected by severity and origin, closed, and the effectiveness of
  corrective actions) and audits (in the period, completed, findings by type);
- **d)** training: sessions, hours and topics;
- **e)** the effectiveness of actions on risks and opportunities: each action
  records its own (see [Actions](#actions)), but the review does not summarize
  it yet, so the comment covers it;
- **f)** improvements implemented in the period.

The figures count the period up to its end, or up to today when the end is
later (the **Fecha de corte**, cut-off date). In a draft they are computed
live and marked **Datos provisionales**. Holding the review freezes them, so
the review and its minutes keep showing what was presented even if the
registers change later.

### Actions (9.3.3)

Under **Salidas: acciones** (outputs: actions), **+ Nueva acción** records a
decision:

- **Tipo**: **Oportunidad de mejora** (improvement opportunity), **Cambio en
  el SGC** (QMS change) or **Necesidad de recursos** (resource need);
- **Descripción**, **Responsable** and **Fecha límite** (deadline);
- **Estado**: **Pendiente**, **En curso** (in progress), **Completada** or
  **Cancelada**;
- **Fecha de finalización** (completion date, only when completed) and
  **Resultado**.

**Rules:**

- Actions are added, edited and deleted while the review is a draft or held.
  Once it is closed, **Seguimiento** (follow-up) changes only the state, the
  completion date and the result.
- A completed action needs its completion date: never in the future, nor
  before the review's date. The review's date cannot move past a completed
  action.
- An action is overdue when it is **Pendiente** or **En curso** after its
  deadline.

### Screens

- **The list** filters by **Año** and **Estado**, and counts each review's
  attendees and its open, overdue and total actions.
- **The review's page** shows the **Reunión** (meeting), the conclusions, the
  inputs with their comments and the actions with an overdue badge.
- **Registro de acciones** (action register, `/revisiones-direccion/acciones`)
  lists every review's actions and filters by **Estado**, **Responsable**,
  **Tipo** and **Vencidas** (overdue).
- The dashboard card **Acciones de la revisión por la dirección** lists the
  overdue actions.

### Minutes PDF

**Acta PDF** on the review's page produces the minutes:

- the meeting: date, period, state, attendees, who held it, the closing date
  and the cut-off date of the data;
- each input with its figures and comment, marked **BORRADOR — datos
  provisionales** while the review is a draft;
- the conclusions;
- the actions: type, description, owner, deadline (and whether overdue),
  state, completion date and result;
- the date the minutes were generated.

## Other registers

These registers predate the modules above. Some have screens; others have
none and are kept through the MCP server (see
[Agents and API tokens](#agents-and-api-tokens) and [`mcp.md`](mcp.md), in
Spanish), which reads and writes them with the same role rules.

| Register | Clause | Where | Fields |
|---|---|---|---|
| **Partes interesadas** (interested parties) | 4.2 | Menu **Clientes** | **Nombre** (unique), **Necesidades y Expectativas**, **Requisitos Identificados**, **Objetivo Estratégico** |
| Roles and responsibilities | 5.3 | No screen; MCP module `roles_responsabilidades` | Role name (unique), commitment to quality, quality policy description |
| **Capacitaciones** (training) | 7.2 | Menu **Personas y competencia** | **Tema** (topic), **Fecha**, **Personal** and **Persona**, **Duración en Horas**, **Evaluación Final** |
| Training resources | 7.1 | No screen; MCP module `recursos_capacitacion` | Resource needed, staff training (yes or no), documentation |
| Process operations | 4.4, 8.1 | No screen; MCP module `procesos` | Process name (unique), quality criterion, supplier control (yes or no), nonconformity |
| Audit indicators | 9.1 | No screen; MCP module `indicadores_auditoria` | Area, date, result, corrective action, performance indicator |
| **Mejoras** (improvements) | 10.3 | Menu **Calidad** | **No conformidad** (required), **Acción correctiva**, **Acción preventiva**; the **Implantada** date is set when the improvement is recorded |
| **Satisfacción** (customer satisfaction) | 9.1.2 | Menu **Clientes** | **Cliente**, **Fecha de Encuesta**, **Puntuación (1-10)**, **Comentarios** |

Notes:

- The QMS roles are what **Personas** and **Competencias requeridas** pick
  from; a role cited by competence requirements cannot be deleted.
- The processes are what quality objectives and risks and opportunities pick
  from; a process they cite cannot be deleted.
- Interested parties, audit indicators, training and improvements feed the
  management review inputs.
- **Capacitaciones** filters by **Tema**, **Fecha** and **Personal**, and each
  session has a **PDF** (**Registro de capacitación**). Demonstrated competence
  can cite a session as evidence.
- **Satisfacción** filters by **Cliente** and **Puntuación mínima** (minimum
  score), and each survey has a **PDF** (**Encuesta de satisfacción**).
- **Mejoras** lists ten improvements per page.
- Every role records training, improvements, surveys and interested parties;
  only administrators delete them. The registers without screens are written
  by administrators and auditors.

## Reports and e-mail notifications

### Monthly quality report

The **Informe mensual de calidad** (monthly quality report, clause 9.1) is a
PDF e-mailed to administrators on the 1st of each month. It covers the
calendar month that has just ended, and is not available from the web
screens.

The header gives the report's period (for example, **Periodo: septiembre de
2026**) and, under **Generado el**, the day it was generated: the 1st on which
it is sent, not the month's last day.

Each figure counts:

| Figure | What it counts |
|---|---|
| **Auditorías** | Audits dated in the month, in any state |
| **No conformidades** | Nonconformities detected in the month |
| **Satisfacción media** | The average score of the surveys dated in the month, out of 10 (0 without surveys) |
| **Capacitaciones** | Training sessions dated in the month |
| **No conformidades por estado** | The month's nonconformities, split by their state when the report is generated |
| **Acciones correctivas verificadas** | Corrective actions whose verification date is in the month, split into **Eficaces** and **No eficaces** |
| **Auditorías completadas** | Audits dated in the month that are **Completada** |
| **Hallazgos de auditoría por tipo** | Findings of the audits dated in the month, by type |
| **Mediciones registradas** | Quality objective measurements dated in the month |
| **Objetivos activos** | Objectives that are **Activo** when the report is generated and started by the month's last day |
| **Objetivos activos que cumplen la meta** | Of those, the ones meeting their target with the measurements up to the month's last day |
| **Objetivos activos con medición vencida** | Of those, the ones with an overdue measurement on the month's last day |

An objective closed after the month ended no longer counts as active, because
the system keeps no date of state changes.

### E-mail notifications

The server sends four scheduled e-mails, at the times below in the
organization's time zone. They go to active users with an e-mail address;
anyone else is skipped. The audit and document e-mails are not sent when
there is nothing to list.
The server side (Redis and Celery) is described in the
[operations guide](operaciones.md#redis-y-celery) (in Spanish).

| Subject | To | When | Contents |
|---|---|---|---|
| **Recordatorio de Auditorías Próximas** | Users with the **Auditor** role | Every day at 07:00 | Audits **Pendiente** or **En Proceso** dated from today to 7 days ahead, with area and date |
| **Reporte de Auditorías Pendientes** | Users with the **Administrador** role | Mondays at 08:00 | Every audit in state **Pendiente**, whatever its date, with number, area and date |
| **Reporte Mensual del Sistema de Gestión de Calidad** | Users with the **Administrador** role | The 1st of each month at 08:00 | The [monthly report](#monthly-quality-report) of the month that ended, attached as `reporte-calidad-YYYY-MM.pdf` |
| **Revisiones periódicas de documentos pendientes** | Administrators and document owners | Mondays at 08:00 | Documents in use whose next review date has arrived, with code, title and review date |

The document review e-mail works per recipient:

- Administrators receive the whole list, with each document's owner.
- Each owner receives their own documents through the user account linked to
  their person (an operativo, only documents in force).
- Owners without a linked user receive nothing.

The password reset e-mails are not scheduled: they are sent when someone asks
for a link or an administrator sends one (see
[Signing in and your account](#signing-in-and-your-account)).

## Glossary

| Spanish | English |
|---|---|
| Acción correctiva | Corrective action |
| Acción planificada | Action planned (nonconformity state) |
| Acta | Minutes (of a management review) |
| Administrador, Auditor, Operativo | The three roles: administrator, auditor, operational staff |
| Alcance | Scope |
| Auditor jefe | Lead auditor |
| Borrador | Draft |
| Capacitación | Training session |
| Causa raíz | Root cause |
| Celebrada | Held (management review) |
| Cerrada | Closed |
| Competencia acreditada | Demonstrated competence |
| Competencia requerida | Required competence |
| Contención | Containment |
| Dar de baja, De baja | Withdraw, withdrawn (document) |
| Eficaz, No eficaz | Effective, not effective |
| En verificación | Under verification (nonconformity state) |
| Encuesta | Customer survey |
| Fecha límite | Deadline |
| Gravedad | Severity |
| Hallazgo | Audit finding |
| Medición | Measurement |
| Meta | Target |
| Mejora | Improvement |
| Nivel, nivel residual | Level of a risk (likelihood times impact), before and after its actions |
| No conformidad | Nonconformity |
| Objetivo de calidad | Quality objective |
| Obsoleto | Obsolete (revision) |
| Origen | Origin |
| Panel | Dashboard |
| Parte interesada | Interested party |
| Persona | Person (who works under the QMS; not a user account) |
| Prioridad | Priority of an opportunity (benefit times feasibility) |
| Programa de auditoría | Audit programme |
| Propietario | Owner (document) |
| Responsable | Responsible person, owner |
| Revisión (documento) | Revision of a document |
| Revisión por la dirección | Management review |
| Revisión vencida | Review overdue (open risk or opportunity past its next review) |
| Riesgo, Oportunidad | Risk, opportunity |
| SGC | QMS (quality management system) |
| Sin valorar | Not assessed yet (risk or opportunity) |
| Vencida | Overdue |
| Vigente | In force |
