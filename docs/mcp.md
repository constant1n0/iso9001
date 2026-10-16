# MCP server

AI agents operate the QMS through an MCP server that acts as a real user. It
runs the same services, policy, audit log and attribution as the web UI, and it
can never delete.

## Architecture

- `app/mcp_server/` is an adapter over `app/services/` (see
  [services](architecture/services.md)). The package is not named `mcp` so it
  cannot shadow the SDK (`mcp==2.3.0`).
- Five generic tools, with the module as a parameter, keep the agent's context
  small:

  | Tool | Purpose |
  |---|---|
  | `qms_modules` | The 22 modules with fields, allowed values, filters and what the caller may do |
  | `qms_list` | One bounded page of a module, with the filters `qms_modules` lists |
  | `qms_get` | One record by `id` |
  | `qms_create` | Create a record |
  | `qms_update` | Overwrite the given fields of a record |

  There is no delete tool. Records never include password or token fields.
- Each call runs in its own Flask application context and session. A write
  commits after the service returns; any error rolls back. Domain errors reach
  the agent with their Spanish message; unexpected errors reach it as a generic
  message and are logged on the server.
- The actor is the token's owner with the token's scopes, channel `mcp`:
  `read` reads, `write` writes, and the owner's role still applies (an
  `OPERATIVO` token cannot write the JSON registers). Audit rows and `created_by`
  / `updated_by` carry the owner.
- Transports: stateless Streamable HTTP at `/mcp` (JSON responses) for remote
  clients, and stdio for a local process. The HTTP app answers the 2025-06-18
  and 2025-11-25 `initialize` handshake and stateless 2026-07-28 requests.

## Modules

`qms_modules` lists 22 modules: `no_conformidades`, `auditorias`,
`programas_auditoria`, `hallazgos_auditoria`, `documentos`,
`capacitaciones`, `satisfaccion_clientes`, `partes_interesadas`, `mejoras`,
`roles_responsabilidades`, `riesgos_oportunidades`, `recursos_capacitacion`,
`procesos`, `indicadores_auditoria`, `personas`, `competencias_requeridas`,
`competencias_acreditadas`, `acciones_correctivas`, `objetivos_calidad`,
`mediciones_objetivo`, `revisiones_direccion` and `acciones_revision`. Each
field has a
`type`, a `required` flag, `allowed` values for enums and, where the flags do
not say enough, a short `note`.

Nonconformities and corrective actions (ISO 9001 clause 10.2):

- `no_conformidades`: `descripcion` and `fecha_detectada` are required;
  `origen` is `auditoria`, `cliente`, `proceso`, `proveedor` or `otro`;
  `gravedad` is `mayor`, `menor` or `observacion`; `contencion` and
  `causa_raiz` are text; `responsable_id` cites a person, and `responsable`
  and `accion_correctiva` are legacy free text. `estado` is never written:
  a record starts `abierta` and then follows its corrective actions. Records
  show the Spanish labels (`"Acción planificada"`, `"Cliente"`) plus
  `fecha_cierre` and `motivo_cancelacion`. Filters: `descripcion`
  (contains), `estado`, `origen`, `gravedad` (member names such as
  `accion_planificada`, `cliente`, `mayor`) and `fecha_detectada`; any other
  enum value is refused with the allowed list. A `cerrada` or `cancelada`
  record refuses updates.
- `acciones_correctivas`: `no_conformidad_id` (an open nonconformity; it
  never changes), `descripcion`, `responsable_id` (the owner, a person),
  `fecha_prevista` and `fecha_realizada` (not before the detection date).
  Records add the derived `estado` (`Planificada`, `Realizada`, `Verificada
  eficaz`, `Verificada no eficaz`) and the verification columns, which no
  tool writes. Filters: `no_conformidad_id`, `responsable_id` and `estado`
  (member names such as `verificada_eficaz`). A verified action is
  read-only, and every write moves the nonconformity's state along.
- Web-only: verifying an action's effectiveness (administrators and
  auditors, never the action's owner), closing a nonconformity once its
  actions proved effective, cancelling it and reopening it. `qms_update`
  refuses `estado` and the verification fields, and there is no delete tool.

Internal audits (ISO 9001 clause 9.2). The three modules share the audits
grant: administrators and auditors read and write, and no token deletes.

- `programas_auditoria`: one programme per year. `anio` (2000-2100),
  `objetivos` and `alcance` are required. Records show `estado` (`"Borrador"`
  or `"Aprobado"`), `aprobado_en` and `aprobado_por_id`, which no tool writes.
  An approved programme's year never changes, nor does a draft's once it has
  audits. Filters: `anio` and `estado` (member names `borrador`,
  `aprobado`).
- `auditorias` adds planning fields to `area_auditada`, `fecha`, `auditor` /
  `auditor_id` (the lead auditor), `resultado`, `accion_correctiva` and
  `estado`:
  - `programa_id` puts the audit in a programme; its `fecha` must fall in the
    programme's year;
  - `alcance` and `criterios` are text;
  - `responsable_area_id` cites the audited-area owner (a person);
  - `equipo_ids` is a list of people that replaces the team; records return
    it too.

  Neither the lead auditor nor a team member may be the audited-area owner.
  Filters: `area`, `auditor`, `estado`, `fecha_inicio`, `fecha_fin` and
  `programa_id`.
- `hallazgos_auditoria`: `auditoria_id` (it never changes; a cancelled audit
  takes no findings), `tipo` (`nc_mayor`, `nc_menor`, `observacion`,
  `oportunidad_mejora`) and `descripcion` are required; `clausula` (dotted
  numbers from 4 to 10, such as `8.5.1`) and `evidencia` are optional.
  - A `nc_mayor` or `nc_menor` finding creates its nonconformity in the same
    write (origin `auditoria`, severity `mayor` or `menor`, the audit's date,
    the audited-area owner as responsible when active). Records return its id
    as `no_conformidad_id`, which no tool writes.
  - Changing the type never deletes that nonconformity; the severity follows
    the type only while the nonconformity is `abierta`.
  - Filters: `auditoria_id` and `tipo` (member names).
- Web-only: approving a programme (administrators) and every delete. The
  audit and programme PDFs are web pages too.

Documents (ISO 9001 clause 7.5):

- `documentos` is read-only through the MCP server: it declares no writable
  field, `qms_modules` shows `create` and `update` as false for every role, and
  `qms_create` and `qms_update` are refused. The revision workflow, file
  attachments and withdrawal are web-only.
- A record has the document's own fields (`code`, `title`, `category`,
  `owner_id`, `next_review_date` and the `withdrawn_*` fields) plus
  `effective_revision`: the revision in force (`numero`, `content`,
  `effective_from`, `legacy_version`), or `null` when there is none. Drafts,
  revisions in review and approved revisions not yet published never reach
  the agent. Administrators and auditors list every document; other roles only
  documents with a revision in force.
- Filters: `category` (member names such as `PROCEDIMIENTO_OPERATIVO`),
  `owner_id` (the owning person) and `status`: `vigente` (in force),
  `sin_publicar` (in use with no revision in force) or `de_baja` (withdrawn).

People and competence (ISO 9001 clauses 5.3 and 7.2):

- `personas`: people who work under the QMS, separate from login users.
  `rol_ids` (a list of role ids) replaces the roles held, `user_id` links at
  most one user, `activo` deactivates; the record also returns `rol_ids`.
- `competencias_requeridas`: competence a role (`rol_id`) requires; `tipo` is
  `educacion`, `formacion`, `habilidad` or `experiencia`.
- `competencias_acreditadas`: competence a person (`persona_id`) has
  demonstrated, optionally for a requirement (`requisito_id`) and with a
  training as evidence (`capacitacion_id`). `evaluacion_eficacia` other than
  `pendiente` needs `fecha_evaluacion` and `evaluador_id` (a person).
- Person fields elsewhere: `persona_id` in `capacitaciones`, `responsable_id`
  in `no_conformidades` and `auditor_id` in `auditorias` are ids of
  `personas` records (an existing person, active when newly chosen; `null`
  clears the link). The free-text `personal`, `responsable` and `auditor` stay
  as legacy names and are never matched to a person. `personal` and `auditor`
  are not flagged required: a create needs them only when no person is cited,
  and a blank name takes the cited person's name (their `note` says so).

The required-versus-demonstrated matrix is a web page
(`/competencias/matriz`); agents read the same data from these modules.

Quality objectives (ISO 9001:2026 clause 6.2). Both modules share one grant:
every role reads, and only ADMIN or AUDITOR tokens (with the `write` scope)
create and update. No token deletes.

- Numbers (`meta`, `valor_inicial`, `valor` and `ultimo_valor`) come back as
  decimal strings with four decimals, such as `"95.0000"`. They are written as
  finite numbers or decimal text, rounded to four decimals.
- Enums come back as their Spanish label (`sentido` shows `"≥"` or `"≤"`); writes
  and filters take the member name.
- `objetivos_calidad`: `titulo`, `indicador`, `meta`, `sentido` (`mayor_igual`
  or `menor_igual`), `responsable_id` (a person), `fecha_inicio`,
  `fecha_limite` and `frecuencia` (`mensual`, `trimestral`, `semestral`,
  `anual`) are required.
  - Optional: `descripcion`, `proceso_id` (a `procesos` record), `unidad`,
    `valor_inicial`, `anio` (2000-2100, by default the year of
    `fecha_inicio`), `recursos` and `evaluacion`.
  - `estado` starts `activo`; `alcanzado`, `no_alcanzado` and `cancelado`
    close the objective, and `activo` reopens it.
  - `fecha_limite` is never before `fecha_inicio`, and the period always keeps
    the objective's measurements inside it. `meta` and `sentido` cannot change
    once the objective has measurements.
  - Records add the summary: `ultimo_valor` and `ultima_fecha` (the latest
    measurement), `cumple` (whether it meets the target), `tendencia`
    (`sube`, `baja` or `estable` from the measurement before), `proxima_medicion`
    and `medicion_vencida`. The next measurement is due one frequency period
    after the latest one, or after `fecha_inicio` without one, and never after
    `fecha_limite`; it is `null` when the objective is not `activo` or was
    measured on its deadline.
  - Filters: `anio`, `estado`, `responsable_id` and `proceso_id`.
- `mediciones_objetivo`: `objetivo_id`, `fecha` and `valor` are required;
  `comentario` and `registrado_por_id` (a person) are optional.
  - The objective must be `activo`, and a measurement never moves to another
    objective.
  - `fecha` lies in the objective's period and is never after the server's
    local date (`APP_TIMEZONE`); there is one measurement per objective and
    date.
  - Records add `cumple`. Filter: `objetivo_id`.
- Web-only: every delete and the yearly objectives PDF.

Management reviews (ISO 9001 clause 9.3). Both modules share one grant: only
ADMIN or AUDITOR tokens read them, and only those tokens (with the `write`
scope) create and update. No token deletes, holds or closes a review.

- `revisiones_direccion`: `fecha` (the meeting), `periodo_desde` and
  `periodo_hasta` are required; the period starts on or before its end, which
  is on or before the meeting.
  - Optional: `asistente_ids` (people; it replaces the attendees), one comment
    per input of 9.3.2 (`comentario_acciones_previas`, `comentario_contexto`,
    `comentario_desempeno`, `comentario_recursos`, `comentario_riesgos`,
    `comentario_mejora`) and `conclusiones`.
  - `estado` (`borrador`, `celebrada`, `cerrada`) is never written. Holding
    a review, which freezes its inputs into `datos_entrada` and records
    `celebrada_por_id`, and closing it, which records `fecha_cierre`, are
    web-only, for administrators.
  - A draft takes every field; a held review only its comments and
    conclusions; a closed one nothing. Moving `fecha` past an action's
    completion date is refused.
  - Records add `asistente_ids` and `preview`: the inputs of the period,
    frozen once held and computed live in a draft. `fecha_corte` is
    `periodo_hasta`, or the server's local date when earlier; each section
    (`acciones_previas`, `contexto`, `satisfaccion`, `objetivos`,
    `indicadores`, `no_conformidades`, `auditorias`, `mejoras`,
    `capacitaciones`) counts the period up to it, and its lists hold at most
    ten items.
  - Filters: `estado` and `anio` (the year of the meeting).
- `acciones_revision`: `revision_id`, `tipo` (`mejora`, `cambio_sgc`,
  `recursos`), `descripcion`, `responsable_id` (a person) and `fecha_limite`
  are required; `estado` (`pendiente` by default, `en_curso`, `completada`,
  `cancelada`), `fecha_completada` and `resultado` are optional.
  - The review must not be closed, and an action never moves to another
    review. Once the review is closed, only `estado`, `fecha_completada` and
    `resultado` change.
  - `fecha_completada` is given exactly when the action is `completada`; it is
    never after the server's local date nor before the review's date.
  - Records add `vencida`: `pendiente` or `en_curso` past `fecha_limite`.
  - Filters: `revision_id`, `estado`, `responsable_id`, `tipo` and `vencida`.
- Web-only: holding, closing, every delete and the minutes PDF.

## Issue a token

From a trusted shell on the server (see [API tokens](operations.md#api-tokens)
in the operations guide for all options):

```bash
venv/bin/flask --app run.py create-api-token --user ana --name "Claude Code" --scope read --scope write --days 90
```

The token (`iso_<prefix>_<secret>`) is printed once. Export it on the machine
that runs the client, never in a file under version control:

```bash
export ISO9001_TOKEN='iso_xxxxxxxx_...'
```

Use a dedicated user per agent with the lowest role that works, and `read`
only when the agent does not need to write. Revoke with `revoke-api-token`.

## Server configuration

Set these in the application's `.env` (the systemd unit reads it):

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY`, `DATABASE_URI` | required | The usual application settings; rotating `SECRET_KEY` invalidates every token |
| `MCP_HOST` | `127.0.0.1` | Interface to bind |
| `MCP_PORT` | `8765` | Port to bind |
| `MCP_ALLOWED_HOSTS` | `127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>` | Comma-separated `Host` values accepted; anything else gets 421. Add the public host name |
| `MCP_TRUSTED_PROXIES` | `127.0.0.1` | Comma-separated proxy addresses whose `X-Forwarded-For` is believed (uvicorn `forwarded_allow_ips`); `*` is refused |
| `ISO9001_MCP_TOKEN` | none | stdio only: the token the process acts as; it refuses to start without a valid one and checks it again on every tool call |
| `MCP_TOKEN_RATE_LIMIT` | `120/minute` | HTTP only: requests per API token, counted after authentication |
| `MCP_AUTH_FAILURE_RATE_LIMIT` | `20/minute` | HTTP only: failed bearer authentications per client address |
| `RATELIMIT_STORAGE_URI` | `memory://` | Where both limits are counted, shared with the web application (Redis in production) |

Run it with the systemd unit `iso9001-mcp.service` (copy it, then
`daemon-reload`, `enable` and `start`, as the header of the file shows), or by
hand:

```bash
venv/bin/python -m app.mcp_server --transport http --host 127.0.0.1 --port 8765
```

stdio re-authenticates the configured token on every tool call, so revoking it,
letting it expire, deactivating its owner or changing the owner's role takes
effect on the next call (a rejected call returns a tool error; the server keeps
running).

`qms_list` pages every module in the database: each service's `list_page`
counts and fetches one page with the same filters and order as its `list_`
(a test checks that every registered service has one).

A missing or invalid token gets `401` with `WWW-Authenticate: Bearer`; failures
are written to the security log (prefix and reason, never the token). Deploying
to production needs its own authorization.

### Rate limits

The HTTP transport applies two limits, written in `limits` notation (one rate
such as `120/minute`; a value that is not exactly one rate of at least one
request stops the server at start-up with exit code 2):

- **Per token** (`MCP_TOKEN_RATE_LIMIT`, default `120/minute`): every
  authenticated request counts against its token. Over the limit the request
  gets `429` and the security log records
  `API_TOKEN_RATE_LIMITED | prefix=<prefix> | ip=<address>`.
- **Per address, failed tokens** (`MCP_AUTH_FAILURE_RATE_LIMIT`, default
  `20/minute`): every failed bearer authentication (missing, malformed, unknown,
  revoked, expired...) counts against the client address, after the token is
  looked up, with one atomic hit, so parallel attempts cannot overshoot it.
  Once an address is over the limit, its failing requests get `429` instead of
  `401` and the security log records `MCP_AUTH_RATE_LIMITED | ip=<address>`
  next to the usual `API_TOKEN_AUTH_FAILED` line. A valid token from the same
  address is never refused by this limit, only by its own per-token limit.
  Requests without a client address share one failure bucket (`-`).

A refusal is `429` with a `Retry-After` header (seconds) and the body
`{"error": "Demasiadas solicitudes. Inténtelo de nuevo más tarde."}`. Requests
under both limits behave exactly as without them. The windows are moving
windows when the storage supports them (memory and Redis do), otherwise fixed
windows, counted in `RATELIMIT_STORAGE_URI` under the keys
`iso9001:mcp:token:<prefix>` and `iso9001:mcp:auth-fail:<address>`; the address
is the one uvicorn resolves, so behind Traefik keep `MCP_TRUSTED_PROXIES` right
or every caller shares the proxy's budget.

The limits fail open: if the storage stops answering (Redis down), requests are
served without limits and the `app.mcp_server.rate_limit` logger writes one
warning naming the error type (never the storage URI), plus one info line when
the storage answers again. A `redis://` or `rediss://` storage whose
`RATELIMIT_STORAGE_OPTIONS` lack `socket_connect_timeout` / `socket_timeout`
gets one second for each, so a stalled Redis delays a request by about a
second instead of the TCP timeout. An unusable
`RATELIMIT_STORAGE_URI` (unknown scheme, malformed, driver not installed) stops
the server at start-up with exit code 2 and a message without its credentials.

stdio has no rate limits: it runs locally as one configured token, so whoever
can start it already has access to the server itself.

## Traefik routing

The server speaks plain HTTP on `127.0.0.1:8765`; Traefik terminates TLS. Route
either a path or a subdomain to that address and keep the original `Host`
header (Traefik's default):

- Path: `PathPrefix(`/mcp`)` on the QMS host, service URL `http://127.0.0.1:8765`.
- Subdomain: `Host(`mcp.qms.example.com`)`, same service URL.

Traefik runs on the same host, so its connection comes from `127.0.0.1` and the
default `MCP_TRUSTED_PROXIES` is right: the security log then records the real
caller from `X-Forwarded-For`. Requests from any other peer cannot set that
header's effect. If the proxy runs elsewhere, list its address in
`MCP_TRUSTED_PROXIES`.

Then set `MCP_ALLOWED_HOSTS` to the public host name (for example
`MCP_ALLOWED_HOSTS=qms.example.com`) and restart the unit. The client URL is
`https://<host>/mcp`. Never expose port 8765 without TLS: the bearer token
travels in a header.

## Client configuration

Replace `https://qms.example.com/mcp` with your URL. Every remote client sends
`Authorization: Bearer <token>`.

### Claude Code

```bash
claude mcp add-json --scope user iso9001 \
  '{"type":"http","url":"https://qms.example.com/mcp","headers":{"Authorization":"Bearer ${ISO9001_TOKEN}"}}'
```

The single quotes keep `${ISO9001_TOKEN}` literal: Claude Code stores the
placeholder and expands it from the environment when it connects, so the token
never lands in `~/.claude.json`. This also works, but the shell expands the
variable first and the token is stored in plain text:

```bash
claude mcp add --transport http iso9001 https://qms.example.com/mcp \
  --header "Authorization: Bearer ${ISO9001_TOKEN}"
```

Avoid `--scope project`: it writes `.mcp.json` into the repository (and needs
per-project approval).

Local stdio instead (the token is read once at start):

```bash
claude mcp add iso9001-local --transport stdio --env ISO9001_MCP_TOKEN="$ISO9001_TOKEN" \
  -- /path/to/iso9001/venv/bin/python -m app.mcp_server --transport stdio
```

### Codex

`~/.codex/config.toml`:

```toml
[mcp_servers.iso9001]
url = "https://qms.example.com/mcp"
bearer_token_env_var = "ISO9001_TOKEN"
```

### Pi

`~/.pi/agent/mcp.json` (or a project `.pi/mcp.json`):

```json
{
  "mcpServers": {
    "iso9001": {
      "url": "https://qms.example.com/mcp",
      "headers": { "Authorization": "Bearer ${ISO9001_TOKEN}" }
    }
  }
}
```

Pi calls MCP tools from its code mode by default (`mcp__iso9001__qms_modules`).
A project `.pi/mcp.json` is read by `pi -p`, but `pi mcp list` ignores it until
the project is trusted.

### OpenCode

`opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "iso9001": {
      "type": "remote",
      "url": "https://qms.example.com/mcp",
      "enabled": true,
      "headers": { "Authorization": "Bearer {env:ISO9001_TOKEN}" }
    }
  }
}
```

`opencode mcp list` shows whether it connected. For one-shot runs
(`opencode run`) close standard input (`< /dev/null`); a run left waiting on it
never reached the server.

### OpenClaw

```json
{
  "mcp": {
    "servers": {
      "iso9001": {
        "transport": "streamable-http",
        "url": "https://qms.example.com/mcp",
        "headers": { "Authorization": "Bearer ${ISO9001_TOKEN}" }
      }
    }
  }
}
```

`openclaw mcp probe iso9001` lists the tools. `openclaw agent` runs through the
gateway, which reads the gateway's own configuration; add `--local` to use the
configuration above directly.

### Claude Desktop

Claude Desktop starts local processes only, so use `mcp-remote` as a bridge.
Keep the token out of the process list with a header file (one `Name: value`
per line, mode `600`):

```bash
umask 077
printf 'Authorization: Bearer %s\n' "$ISO9001_TOKEN" > ~/.iso9001-mcp-headers
```

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "iso9001": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote", "https://qms.example.com/mcp",
        "--transport", "http-only",
        "--header-file", "/home/you/.iso9001-mcp-headers"
      ]
    }
  }
}
```

### Local stdio (any client that launches a command)

```json
{
  "mcpServers": {
    "iso9001-local": {
      "command": "/path/to/iso9001/venv/bin/python",
      "args": ["-m", "app.mcp_server", "--transport", "stdio"],
      "cwd": "/path/to/iso9001",
      "env": { "ISO9001_MCP_TOKEN": "<paste the token here>" }
    }
  }
}
```

### Verification status

- **Claude Code**: connected to production over Streamable HTTP with a
  personal token, reported by the maintainer (2026-10-06).
- **Codex 0.157, OpenCode 1.18, Pi 1.0, OpenClaw 2026.7 and Claude Code**: the
  snippets above (with a local URL) connected to a local server built from
  `main@69bcfa7` and called `qms_modules` with a read-only token, each through
  its normal one-shot command; the server log recorded the calls (2026-10-06).
  Each client's `${...}` or `{env:...}` placeholder was expanded from the
  environment.
- **Claude Desktop through `mcp-remote`**: not tested (no Linux build); the
  `--header` / `--header-file` options follow the `mcp-remote` v0.14.3
  documentation.

The server is stateless: it sends no `Mcp-Session-Id` and answers with JSON.

## Smoke test

Per client, after configuring it with a `read` token for a user that can read
non-conformities:

1. **Claude Code:** `claude mcp list` shows `iso9001` connected; ask it to run
   `qms_modules`, then list non-conformities.
2. **Codex:** `codex mcp list` shows the server; ask it to call `qms_modules`.
3. **Pi:** open the MCP panel (`/mcp`) and check the five `qms_*` tools; call
   `qms_modules`.
4. **OpenCode:** `opencode mcp list` shows `iso9001` connected; call `qms_modules`.
5. **OpenClaw:** check the server is listed with the five tools; call `qms_modules`.
6. **Claude Desktop:** restart it, check the tools icon lists `iso9001`, and ask
   for the modules.

Then, once, with a `write` token for a test user: create a non-conformity, read
it back with `qms_get` and confirm the audit row has channel `mcp`. A wrong
token must give `401` (for example `curl -i -X POST https://qms.example.com/mcp`
without a header).
