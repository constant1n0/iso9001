# Servidor MCP

Los agentes de IA trabajan con el SGC a través de un servidor MCP que actúa
como un usuario real. Usa los mismos servicios, la misma política de permisos,
el mismo registro de auditoría y la misma atribución que la interfaz web, y
nunca puede eliminar.

## Contenido

- [Arquitectura](#arquitectura)
- [Módulos](#módulos)
- [Emisión de un token](#emisión-de-un-token)
- [Configuración del servidor](#configuración-del-servidor)
  - [Límites de peticiones](#límites-de-peticiones)
- [Enrutamiento con Traefik](#enrutamiento-con-traefik)
- [Configuración de los clientes](#configuración-de-los-clientes)
  - [Claude Code](#claude-code)
  - [Codex](#codex)
  - [Pi](#pi)
  - [OpenCode](#opencode)
  - [OpenClaw](#openclaw)
  - [Claude Desktop](#claude-desktop)
  - [stdio local (cualquier cliente que lance una orden)](#stdio-local-cualquier-cliente-que-lance-una-orden)
  - [Estado de la verificación](#estado-de-la-verificación)
- [Prueba de humo](#prueba-de-humo)

## Arquitectura

- `app/mcp_server/` es un adaptador sobre `app/services/` (véase la
  [capa de servicios](architecture/services.md), en inglés). El paquete no se
  llama `mcp` para no ocultar el SDK (`mcp==2.3.0`).
- Cinco herramientas genéricas, con el módulo como parámetro, mantienen
  reducido el contexto del agente:

  | Herramienta | Finalidad |
  |---|---|
  | `qms_modules` | Los 24 módulos con sus campos, los valores admitidos, los filtros y lo que puede hacer quien llama |
  | `qms_list` | Una página acotada de un módulo, con los filtros que enumera `qms_modules` |
  | `qms_get` | Un registro por su `id` |
  | `qms_create` | Crea un registro |
  | `qms_update` | Sobrescribe los campos indicados de un registro |

  No existe ninguna herramienta de eliminación. Los registros nunca incluyen
  campos de contraseña ni de token.
- Cada llamada se ejecuta en su propio contexto de aplicación de Flask y en su
  propia sesión. Una escritura se confirma cuando el servicio termina; cualquier
  error la deshace. Los errores de dominio llegan al agente con su mensaje en
  español; los errores inesperados le llegan como un mensaje genérico y se
  registran en el servidor.
- Actúa el propietario del token, con los ámbitos del token y el canal `mcp`:
  `read` permite leer y `write` escribir, y el rol del propietario sigue
  aplicándose (un token de un usuario `OPERATIVO` no puede escribir en un
  registro que su rol solo consulta, como los procesos o los roles). Las filas de auditoría y `created_by` / `updated_by`
  registran al propietario.
- Transportes: Streamable HTTP sin estado en `/mcp` (respuestas JSON) para los
  clientes remotos, y stdio para un proceso local. La aplicación HTTP atiende
  la negociación `initialize` de las versiones 2025-06-18 y 2025-11-25 y las
  peticiones sin estado de la versión 2026-07-28.

## Módulos

`qms_modules` enumera 24 módulos: `no_conformidades`, `auditorias`,
`programas_auditoria`, `hallazgos_auditoria`, `documentos`,
`capacitaciones`, `satisfaccion_clientes`, `partes_interesadas`, `mejoras`,
`roles_responsabilidades`, `recursos_capacitacion`, `procesos`,
`indicadores_auditoria`, `personas`, `competencias_requeridas`,
`competencias_acreditadas`, `acciones_correctivas`, `objetivos_calidad`,
`mediciones_objetivo`, `revisiones_direccion`, `acciones_revision`,
`riesgos`, `oportunidades` y `acciones_riesgo`. Cada
campo tiene un `type`, un indicador `required`, los valores `allowed` de los
enumerados y, cuando los indicadores no bastan, una breve `note`.

No conformidades y acciones correctivas (cláusula 10.2 de ISO 9001):

- `no_conformidades`: `descripcion` y `fecha_detectada` son obligatorios;
  `origen` es `auditoria`, `cliente`, `proceso`, `proveedor` u `otro`;
  `gravedad` es `mayor`, `menor` u `observacion`; `contencion` y
  `causa_raiz` son texto; `responsable_id` cita a una persona, y `responsable`
  y `accion_correctiva` son texto libre heredado. `estado` nunca se escribe:
  un registro empieza en `abierta` y después sigue a sus acciones correctivas.
  Los registros muestran las etiquetas en español (`"Acción planificada"`,
  `"Cliente"`) además de `fecha_cierre` y `motivo_cancelacion`. Filtros:
  `descripcion` (contiene), `estado`, `origen`, `gravedad` (nombres de miembro
  como `accion_planificada`, `cliente`, `mayor`) y `fecha_detectada`;
  cualquier otro valor de enumerado se rechaza con la lista de valores
  admitidos. Un registro `cerrada` o `cancelada` rechaza las actualizaciones.
- `acciones_correctivas`: `no_conformidad_id` (una no conformidad abierta;
  nunca cambia), `descripcion`, `responsable_id` (el responsable, una
  persona), `fecha_prevista` y `fecha_realizada` (no anterior a la fecha de
  detección). Los registros añaden el `estado` derivado (`Planificada`,
  `Realizada`, `Verificada eficaz`, `Verificada no eficaz`) y las columnas de
  verificación, que ninguna herramienta escribe. Filtros: `no_conformidad_id`,
  `responsable_id` y `estado` (nombres de miembro como `verificada_eficaz`).
  Una acción verificada es de solo lectura, y cada escritura hace avanzar el
  estado de la no conformidad.
- Solo en la web: verificar la eficacia de una acción (administradores y
  auditores, nunca el responsable de la acción), cerrar una no conformidad
  cuando sus acciones han demostrado ser eficaces, cancelarla y reabrirla.
  `qms_update` rechaza `estado` y los campos de verificación, y no existe
  ninguna herramienta de eliminación.

Auditorías internas (cláusula 9.2 de ISO 9001). Los tres módulos comparten el
permiso de las auditorías: los administradores y los auditores leen y
escriben, y ningún token elimina.

- `programas_auditoria`: un programa por año. `anio` (2000-2100),
  `objetivos` y `alcance` son obligatorios. Los registros muestran `estado`
  (`"Borrador"` o `"Aprobado"`), `aprobado_en` y `aprobado_por_id`, que
  ninguna herramienta escribe. El año de un programa aprobado nunca cambia, ni
  tampoco el de un borrador una vez que tiene auditorías. Filtros: `anio` y
  `estado` (nombres de miembro `borrador`, `aprobado`).
- `auditorias` añade campos de planificación a `area_auditada`, `fecha`,
  `auditor` / `auditor_id` (el auditor jefe), `resultado`,
  `accion_correctiva` y `estado`:
  - `programa_id` incluye la auditoría en un programa; su `fecha` debe caer
    dentro del año del programa;
  - `alcance` y `criterios` son texto;
  - `responsable_area_id` cita al responsable del área auditada (una
    persona);
  - `equipo_ids` es una lista de personas que sustituye al equipo; los
    registros también la devuelven.

  Ni el auditor jefe ni un miembro del equipo pueden ser el responsable del
  área auditada. Filtros: `area`, `auditor`, `estado`, `fecha_inicio`,
  `fecha_fin` y `programa_id`.
- `hallazgos_auditoria`: `auditoria_id` (nunca cambia; una auditoría
  cancelada no admite hallazgos), `tipo` (`nc_mayor`, `nc_menor`,
  `observacion`, `oportunidad_mejora`) y `descripcion` son obligatorios;
  `clausula` (números separados por puntos del 4 al 10, como `8.5.1`) y
  `evidencia` son opcionales.
  - Un hallazgo `nc_mayor` o `nc_menor` crea su no conformidad en la misma
    escritura (origen `auditoria`, gravedad `mayor` o `menor`, la fecha de la
    auditoría y, como responsable, el responsable del área auditada cuando
    está activo). Los registros devuelven su id como `no_conformidad_id`, que
    ninguna herramienta escribe.
  - Cambiar el tipo nunca elimina esa no conformidad; la gravedad sigue al
    tipo solo mientras la no conformidad está `abierta`.
  - Filtros: `auditoria_id` y `tipo` (nombres de miembro).
- Solo en la web: aprobar un programa (administradores) y todas las
  eliminaciones. Los PDF de la auditoría y del programa también son páginas
  web.

Documentos (cláusula 7.5 de ISO 9001):

- `documentos` es de solo lectura a través del servidor MCP: no declara ningún
  campo escribible, `qms_modules` muestra `create` y `update` como falsos para
  todos los roles, y `qms_create` y `qms_update` se rechazan. El flujo de
  revisiones, los ficheros adjuntos y la baja solo están en la web.
- Un registro tiene los campos propios del documento (`code`, `title`,
  `category`, `owner_id`, `next_review_date` y los campos `withdrawn_*`)
  además de `effective_revision`: la revisión vigente (`numero`, `content`,
  `effective_from`, `legacy_version`), o `null` cuando no hay ninguna. Los
  borradores, las revisiones pendientes de aprobación y las revisiones
  aprobadas aún no publicadas nunca llegan al agente. Los administradores y
  los auditores ven todos los documentos; los demás roles, solo los documentos
  con una revisión vigente.
- Filtros: `category` (nombres de miembro como `PROCEDIMIENTO_OPERATIVO`),
  `owner_id` (la persona propietaria) y `status`: `vigente` (en vigor),
  `sin_publicar` (en uso sin revisión vigente) o `de_baja` (retirado).

Personas y competencia (cláusulas 5.3 y 7.2 de ISO 9001):

- `personas`: las personas que trabajan bajo el SGC, distintas de los
  usuarios que inician sesión. `rol_ids` (una lista de ids de rol) sustituye
  los roles que desempeña, `user_id` vincula como máximo un usuario y `activo`
  la desactiva; el registro también devuelve `rol_ids`.
- `competencias_requeridas`: la competencia que exige un rol (`rol_id`);
  `tipo` es `educacion`, `formacion`, `habilidad` o `experiencia`.
- `competencias_acreditadas`: la competencia que una persona (`persona_id`)
  ha demostrado, opcionalmente para un requisito (`requisito_id`) y con una
  capacitación como evidencia (`capacitacion_id`). Un `evaluacion_eficacia`
  distinto de `pendiente` necesita `fecha_evaluacion` y `evaluador_id` (una
  persona).
- Campos de persona en otros módulos: `persona_id` en `capacitaciones`,
  `responsable_id` en `no_conformidades` y `auditor_id` en `auditorias` son
  ids de registros de `personas` (una persona existente, que debe estar activa
  cuando es una nueva elección; `null` elimina el vínculo). Los campos de
  texto libre `personal`, `responsable` y `auditor` se mantienen como nombres
  heredados y nunca se asocian a una persona. `personal` y `auditor` no están
  marcados como obligatorios: una creación solo los necesita cuando no se cita
  ninguna persona, y un nombre en blanco toma el nombre de la persona citada
  (su `note` lo indica).

La matriz de competencias requeridas frente a demostradas es una página web
(`/competencias/matriz`); los agentes leen los mismos datos desde estos
módulos.

Procesos y roles (cláusulas 4.4, 5.3 y 8.1 de ISO 9001:2026). Todos los roles
leen ambos módulos, y solo los tokens de ADMIN o AUDITOR (con el ámbito
`write`) crean y actualizan. Ningún token elimina.

- `procesos`: `proceso` (único) es obligatorio.
  - `tipo` es `estrategico`, `operativo` o `soporte`; los registros muestran
    la etiqueta en español (`"Estratégico"`), y las escrituras y los filtros
    usan el nombre del miembro.
  - `responsable_id` cita a la persona responsable del proceso, que después
    no puede eliminarse.
  - `entradas`, `salidas`, `recursos`, `criterio_calidad` (los criterios de
    operación, 8.1), `criterios_aceptacion` (los de sus salidas),
    `control_proveedor` y `no_conformidad` son opcionales.
  - `relacionados` es una lista de ids de `procesos` que sustituye a la
    actual; una lista vacía quita todas las relaciones. La relación es mutua:
    cada proceso relacionado también lista este. Un proceso nunca se relaciona
    consigo mismo. Los registros también devuelven `relacionados`.
  - Filtros: `tipo` y `responsable_id`.
- `roles_responsabilidades`: `rol` (único) es obligatorio;
  `responsabilidades`, `autoridades`, `compromiso_calidad` y
  `descripcion_politica_calidad` son opcionales. `reporta_a_id` es el rol al
  que reporta: nunca él mismo ni un rol que le reporta, directa o
  indirectamente (`null` lo deja sin línea de reporte).
- Solo en la web: las eliminaciones, que rechazan un proceso relacionado con
  otros o citado por objetivos, riesgos u oportunidades, y un rol con
  requisitos de competencia o con roles que le reportan. Las páginas
  `/procesos/` y `/roles/` muestran además los registros que citan un proceso,
  y la línea de reporte, las personas y los requisitos de competencia de un
  rol.

Objetivos de la calidad (cláusula 6.2 de ISO 9001:2026). Ambos módulos
comparten un mismo permiso: todos los roles leen, y solo los tokens de ADMIN o
AUDITOR (con el ámbito `write`) crean y actualizan. Ningún token elimina.

- Los números (`meta`, `valor_inicial`, `valor` y `ultimo_valor`) se
  devuelven como cadenas decimales con cuatro decimales, como `"95.0000"`. Se
  escriben como números finitos o como texto decimal, redondeados a cuatro
  decimales.
- Los enumerados se devuelven con su etiqueta en español (`sentido` muestra
  `"≥"` o `"≤"`); las escrituras y los filtros usan el nombre del miembro.
- `objetivos_calidad`: `titulo`, `indicador`, `meta`, `sentido`
  (`mayor_igual` o `menor_igual`), `responsable_id` (una persona),
  `fecha_inicio`, `fecha_limite` y `frecuencia` (`mensual`, `trimestral`,
  `semestral`, `anual`) son obligatorios.
  - Opcionales: `descripcion`, `proceso_id` (un registro de `procesos`),
    `unidad`, `valor_inicial`, `anio` (2000-2100; por defecto, el año de
    `fecha_inicio`), `recursos` y `evaluacion`.
  - `estado` empieza en `activo`; `alcanzado`, `no_alcanzado` y `cancelado`
    cierran el objetivo, y `activo` lo reabre.
  - `fecha_limite` nunca es anterior a `fecha_inicio`, y el periodo siempre
    deja dentro las mediciones del objetivo. `meta` y `sentido` no pueden
    cambiar una vez que el objetivo tiene mediciones.
  - Los registros añaden el resumen: `ultimo_valor` y `ultima_fecha` (la
    última medición), `cumple` (si alcanza la meta), `tendencia` (`sube`,
    `baja` o `estable` respecto a la medición anterior), `proxima_medicion` y
    `medicion_vencida`. La siguiente medición vence un periodo de la
    frecuencia después de la última, o después de `fecha_inicio` si no hay
    ninguna, y nunca después de `fecha_limite`; es `null` cuando el objetivo
    no está `activo` o se midió en su fecha límite.
  - Filtros: `anio`, `estado`, `responsable_id` y `proceso_id`.
- `mediciones_objetivo`: `objetivo_id`, `fecha` y `valor` son obligatorios;
  `comentario` y `registrado_por_id` (una persona) son opcionales.
  - El objetivo debe estar `activo`, y una medición nunca pasa a otro
    objetivo.
  - `fecha` cae dentro del periodo del objetivo y nunca es posterior a la
    fecha local del servidor (`APP_TIMEZONE`); hay una medición por objetivo y
    fecha.
  - Los registros añaden `cumple`. Filtro: `objetivo_id`.
- Solo en la web: todas las eliminaciones y el PDF anual de objetivos.

Revisiones por la dirección (cláusula 9.3 de ISO 9001). Ambos módulos
comparten un mismo permiso: solo los tokens de ADMIN o AUDITOR los leen, y
solo esos tokens (con el ámbito `write`) crean y actualizan. Ningún token
elimina, celebra ni cierra una revisión.

- `revisiones_direccion`: `fecha` (la reunión), `periodo_desde` y
  `periodo_hasta` son obligatorios; el periodo no empieza después de su fin, y
  este no es posterior a la reunión.
  - Opcionales: `asistente_ids` (personas; sustituye a los asistentes), un
    comentario por cada entrada de 9.3.2 (`comentario_acciones_previas`,
    `comentario_contexto`, `comentario_desempeno`, `comentario_recursos`,
    `comentario_riesgos`, `comentario_mejora`) y `conclusiones`.
  - `estado` (`borrador`, `celebrada`, `cerrada`) nunca se escribe. Celebrar
    una revisión, lo que congela sus entradas en `datos_entrada` y registra
    `celebrada_por_id`, y cerrarla, lo que registra `fecha_cierre`, solo se
    hacen en la web y corresponden a los administradores.
  - Un borrador admite todos los campos; una revisión celebrada, solo sus
    comentarios y conclusiones; una cerrada, ninguno. Se rechaza llevar
    `fecha` más allá de la fecha de finalización de una acción.
  - Los registros añaden `asistente_ids` y `preview`: las entradas del
    periodo, congeladas una vez celebrada la revisión y calculadas en vivo en
    un borrador. `fecha_corte` es `periodo_hasta`, o la fecha local del
    servidor cuando es anterior; cada sección (`acciones_previas`,
    `contexto`, `satisfaccion`, `objetivos`, `indicadores`,
    `no_conformidades`, `auditorias`, `mejoras`, `capacitaciones`) recuenta el
    periodo hasta esa fecha, y sus listas contienen como máximo diez
    elementos.
  - Filtros: `estado` y `anio` (el año de la reunión).
- `acciones_revision`: `revision_id`, `tipo` (`mejora`, `cambio_sgc`,
  `recursos`), `descripcion`, `responsable_id` (una persona) y `fecha_limite`
  son obligatorios; `estado` (`pendiente` por defecto, `en_curso`,
  `completada`, `cancelada`), `fecha_completada` y `resultado` son opcionales.
  - La revisión no debe estar cerrada, y una acción nunca pasa a otra
    revisión. Una vez cerrada la revisión, solo cambian `estado`,
    `fecha_completada` y `resultado`.
  - `fecha_completada` se indica exactamente cuando la acción está
    `completada`; nunca es posterior a la fecha local del servidor ni anterior
    a la fecha de la revisión.
  - Los registros añaden `vencida`: `pendiente` o `en_curso` con la
    `fecha_limite` superada.
  - Filtros: `revision_id`, `estado`, `responsable_id`, `tipo` y `vencida`.
- Solo en la web: celebrar, cerrar, todas las eliminaciones y el PDF del acta.

Riesgos y oportunidades (cláusula 6.1 de ISO 9001:2026). Los tres módulos
sustituyen al antiguo `riesgos_oportunidades` y comparten un mismo permiso:
todos los roles leen, y solo los tokens de ADMIN o AUDITOR (con el ámbito
`write`) crean y actualizan. Ningún token elimina.

- Los enumerados se devuelven con su etiqueta en español; las escrituras y
  los filtros usan el nombre del miembro.
- `riesgos` y `oportunidades` comparten estos campos: `titulo`, `descripcion`,
  `origen` (`interno`, `externo`) y `responsable_id` (una persona) son
  obligatorios; `parte_interesada_id` (un registro de `partes_interesadas`),
  `proceso_id` (un registro de `procesos`), `estado`,
  `fecha_identificacion` (por defecto, hoy; nunca futura), `fecha_revision`
  (la próxima revisión; nunca anterior a la identificación) y `notas` son
  opcionales. Los registros migrados del registro antiguo pueden no tener
  responsable.
- Cada valoración es una pareja de enteros de 1 a 5 que se indica entera o se
  deja vacía (sin valorar).
- `riesgos`: `probabilidad` e `impacto`; `tratamiento` (`aceptar`,
  `mitigar`, `transferir`, `evitar`); `probabilidad_residual` e
  `impacto_residual`, que necesitan antes la primera pareja. `estado` empieza
  en `identificado` y pasa por `en_tratamiento`, `controlado` y `cerrado`.
  - Los registros añaden `nivel` y `nivel_residual`, del producto de la
    pareja (`"Bajo"` 1-4, `"Medio"` 5-9, `"Alto"` 10-16, `"Crítico"` 20-25;
    `null` sin valorar), y `revision_vencida` (abierto, con `fecha_revision`
    anterior a hoy).
  - Filtros: `estado`, `nivel` (`bajo`, `medio`, `alto`, `critico`),
    `responsable_id`, `proceso_id`, `origen`, `sin_valorar` y
    `revision_vencida`.
- `oportunidades`: `beneficio` y `viabilidad`; `decision` (`aprovechar`,
  `posponer`, `descartar`). `estado` empieza en `identificada` y pasa por
  `en_curso`, `aprovechada` y `descartada`; las dos últimas la cierran.
  - Los registros añaden `prioridad` (`"Baja"` 1-6, `"Media"` 8-12,
    `"Alta"` 15-25; `null` sin valorar) y `revision_vencida`.
  - Filtros: `estado`, `prioridad` (`baja`, `media`, `alta`),
    `responsable_id`, `proceso_id`, `origen`, `sin_valorar` y
    `revision_vencida`.
- `acciones_riesgo`: `riesgo_id` u `oportunidad_id` (uno de los dos; nunca
  cambia), `descripcion`, `responsable_id` (una persona) y `fecha_limite` son
  obligatorios.
  - Un riesgo `cerrado` o una oportunidad `aprovechada` o `descartada` no
    admite acciones nuevas.
  - `estado` empieza en `pendiente` (`en_curso`, `completada`, `cancelada`);
    `fecha_completada` se indica exactamente cuando la acción está
    `completada` y nunca es futura.
  - La eficacia (6.1.2 b) se evalúa solo en una acción `completada`:
    `eficacia` (`eficaz`, `no_eficaz`) y `fecha_evaluacion` van juntas, la
    fecha no es anterior a `fecha_completada` ni futura, y
    `comentario_eficacia` es opcional.
  - Los registros añaden `vencida`: `pendiente` o `en_curso` con la
    `fecha_limite` superada.
  - Filtros: `riesgo_id`, `oportunidad_id`, `estado`, `responsable_id` y
    `vencida`.
- «Hoy» es la fecha local del servidor (`APP_TIMEZONE`).
- Solo en la web: todas las eliminaciones, el mapa de calor y los PDF de los
  registros; los filtros `abierto`, `probabilidad`, `impacto` y
  `alto_sin_accion` son de la lista web.

## Emisión de un token

Desde una consola de confianza en el servidor (véase
[Tokens de API](operaciones.md#tokens-de-api) en la guía de operaciones, con
todas las opciones):

```bash
venv/bin/flask --app run.py create-api-token --user ana --name "Claude Code" --scope read --scope write --days 90
```

El token (`iso_<prefix>_<secret>`) se imprime una sola vez. Expórtelo en la
máquina que ejecuta el cliente, nunca en un fichero sujeto al control de
versiones:

```bash
export ISO9001_TOKEN='iso_xxxxxxxx_...'
```

Use un usuario dedicado para cada agente, con el rol más bajo que funcione, y
solo `read` cuando el agente no necesite escribir. Revóquelo con
`revoke-api-token`.

## Configuración del servidor

Defina estas variables en el `.env` de la aplicación (la unidad de systemd lo
lee):

| Variable | Valor por defecto | Significado |
|---|---|---|
| `SECRET_KEY`, `DATABASE_URI` | obligatorio | Los ajustes habituales de la aplicación; cambiar `SECRET_KEY` invalida todos los tokens |
| `MCP_HOST` | `127.0.0.1` | Interfaz en la que escucha |
| `MCP_PORT` | `8765` | Puerto en el que escucha |
| `MCP_ALLOWED_HOSTS` | `127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>` | Valores de `Host` admitidos, separados por comas; cualquier otro recibe 421. Añada el nombre de host público |
| `MCP_TRUSTED_PROXIES` | `127.0.0.1` | Direcciones de proxy, separadas por comas, cuyo `X-Forwarded-For` se acepta (`forwarded_allow_ips` de uvicorn); `*` se rechaza |
| `ISO9001_MCP_TOKEN` | ninguno | Solo stdio: el token con el que actúa el proceso; no arranca sin uno válido y lo vuelve a comprobar en cada llamada a una herramienta |
| `MCP_TOKEN_RATE_LIMIT` | `120/minute` | Solo HTTP: peticiones por token de API, contadas después de la autenticación |
| `MCP_AUTH_FAILURE_RATE_LIMIT` | `20/minute` | Solo HTTP: autenticaciones *bearer* fallidas por dirección de cliente |
| `RATELIMIT_STORAGE_URI` | `memory://` | Dónde se cuentan ambos límites; lo comparte con la aplicación web (Redis en producción) |

Ejecútelo con la unidad de systemd `iso9001-mcp.service` (cópiela y después
ejecute `daemon-reload`, `enable` y `start`, como indica la cabecera del
fichero), o a mano:

```bash
venv/bin/python -m app.mcp_server --transport http --host 127.0.0.1 --port 8765
```

stdio vuelve a autenticar el token configurado en cada llamada a una
herramienta, de modo que revocarlo, dejar que caduque, desactivar a su
propietario o cambiar el rol del propietario surte efecto en la siguiente
llamada (una llamada rechazada devuelve un error de herramienta; el servidor
sigue en marcha).

`qms_list` pagina todos los módulos en la base de datos: el `list_page` de
cada servicio cuenta y obtiene una página con los mismos filtros y el mismo
orden que su `list_` (una prueba comprueba que todos los servicios
registrados lo tienen).

Un token ausente o no válido recibe `401` con `WWW-Authenticate: Bearer`; los
fallos se escriben en el registro de seguridad (prefijo y motivo, nunca el
token). El despliegue en producción necesita su propia autorización.

### Límites de peticiones

El transporte HTTP aplica dos límites, escritos en la notación de `limits`
(una sola tasa, como `120/minute`; un valor que no sea exactamente una tasa de
al menos una petición detiene el servidor al arrancar con el código de salida
2):

- **Por token** (`MCP_TOKEN_RATE_LIMIT`, por defecto `120/minute`): cada
  petición autenticada cuenta para su token. Por encima del límite, la
  petición recibe `429` y el registro de seguridad anota
  `API_TOKEN_RATE_LIMITED | prefix=<prefix> | ip=<address>`.
- **Por dirección, tokens fallidos** (`MCP_AUTH_FAILURE_RATE_LIMIT`, por
  defecto `20/minute`): cada autenticación *bearer* fallida (ausente, mal
  formada, desconocida, revocada, caducada...) cuenta para la dirección del
  cliente, después de buscar el token, con un único incremento atómico, de
  modo que los intentos en paralelo no pueden sobrepasarlo. Una vez que una
  dirección supera el límite, sus peticiones fallidas reciben `429` en lugar
  de `401` y el registro de seguridad anota `MCP_AUTH_RATE_LIMITED | ip=<address>`
  junto a la línea habitual `API_TOKEN_AUTH_FAILED`. Este límite nunca rechaza
  un token válido desde la misma dirección; solo lo hace su propio límite por
  token. Las peticiones sin dirección de cliente comparten un mismo contador
  de fallos (`-`).

Un rechazo es un `429` con una cabecera `Retry-After` (en segundos) y el
cuerpo `{"error": "Demasiadas solicitudes. Inténtelo de nuevo más tarde."}`.
Las peticiones por debajo de ambos límites se comportan exactamente igual que
sin ellos. Las ventanas son deslizantes cuando el almacenamiento lo admite
(memoria y Redis lo admiten) y fijas en caso contrario, y se cuentan en
`RATELIMIT_STORAGE_URI` con las claves `iso9001:mcp:token:<prefix>` e
`iso9001:mcp:auth-fail:<address>`; la dirección es la que resuelve uvicorn,
así que, detrás de Traefik, mantenga `MCP_TRUSTED_PROXIES` correcto o todos
los clientes compartirán el cupo del proxy.

Los límites fallan en abierto (*fail open*): si el almacenamiento deja de
responder (Redis caído), las peticiones se atienden sin límites y el
registrador `app.mcp_server.rate_limit` escribe un aviso con el tipo de error
(nunca el URI del almacenamiento), más una línea informativa cuando el
almacenamiento vuelve a responder. Un almacenamiento `redis://` o `rediss://`
cuyas `RATELIMIT_STORAGE_OPTIONS` no incluyan `socket_connect_timeout` /
`socket_timeout` recibe un segundo para cada uno, de modo que un Redis
bloqueado retrasa una petición alrededor de un segundo en lugar del tiempo de
espera de TCP. Un `RATELIMIT_STORAGE_URI` inutilizable (esquema desconocido,
mal formado, controlador no instalado) detiene el servidor al arrancar con el
código de salida 2 y un mensaje sin sus credenciales.

stdio no tiene límites de peticiones: se ejecuta en local con un único token
configurado, así que quien puede iniciarlo ya tiene acceso al propio servidor.

## Enrutamiento con Traefik

El servidor habla HTTP sin cifrar en `127.0.0.1:8765`; Traefik termina TLS.
Encamine una ruta o un subdominio a esa dirección y conserve la cabecera
`Host` original (el comportamiento por defecto de Traefik):

- Ruta: `PathPrefix(`/mcp`)` en el host del SGC, URL del servicio `http://127.0.0.1:8765`.
- Subdominio: `Host(`mcp.qms.example.com`)`, la misma URL del servicio.

Traefik se ejecuta en la misma máquina, así que su conexión procede de
`127.0.0.1` y el valor por defecto de `MCP_TRUSTED_PROXIES` es correcto: el
registro de seguridad anota entonces el cliente real a partir de
`X-Forwarded-For`. Esa cabecera no tiene efecto en las peticiones de ningún
otro par. Si el proxy se ejecuta en otra máquina, incluya su dirección en
`MCP_TRUSTED_PROXIES`.

A continuación, defina `MCP_ALLOWED_HOSTS` con el nombre de host público (por
ejemplo, `MCP_ALLOWED_HOSTS=qms.example.com`) y reinicie la unidad. La URL del
cliente es `https://<host>/mcp`. Nunca exponga el puerto 8765 sin TLS: el
token *bearer* viaja en una cabecera.

## Configuración de los clientes

Sustituya `https://qms.example.com/mcp` por su URL. Todos los clientes remotos
envían `Authorization: Bearer <token>`.

### Claude Code

```bash
claude mcp add-json --scope user iso9001 \
  '{"type":"http","url":"https://qms.example.com/mcp","headers":{"Authorization":"Bearer ${ISO9001_TOKEN}"}}'
```

Las comillas simples mantienen literal `${ISO9001_TOKEN}`: Claude Code guarda
el marcador de posición y lo expande desde el entorno al conectarse, de modo
que el token nunca llega a `~/.claude.json`. Lo siguiente también funciona,
pero la shell expande antes la variable y el token se guarda en texto plano:

```bash
claude mcp add --transport http iso9001 https://qms.example.com/mcp \
  --header "Authorization: Bearer ${ISO9001_TOKEN}"
```

Evite `--scope project`: escribe `.mcp.json` en el repositorio (y necesita una
aprobación por proyecto).

Como alternativa, stdio local (el token se lee una sola vez al arrancar):

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

`~/.pi/agent/mcp.json` (o un `.pi/mcp.json` de proyecto):

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

Pi llama a las herramientas MCP desde su modo de código por defecto
(`mcp__iso9001__qms_modules`). `pi -p` lee un `.pi/mcp.json` de proyecto, pero
`pi mcp list` lo ignora hasta que el proyecto se marca como de confianza.

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

`opencode mcp list` muestra si se ha conectado. Para las ejecuciones puntuales
(`opencode run`), cierre la entrada estándar (`< /dev/null`); una ejecución que
se quedó esperándola nunca llegó al servidor.

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

`openclaw mcp probe iso9001` enumera las herramientas. `openclaw agent` se
ejecuta a través de la pasarela (*gateway*), que lee su propia configuración;
añada `--local` para usar directamente la configuración anterior.

### Claude Desktop

Claude Desktop solo inicia procesos locales, así que use `mcp-remote` como
puente. Mantenga el token fuera de la lista de procesos con un fichero de
cabeceras (un `Name: value` por línea, modo `600`):

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

### stdio local (cualquier cliente que lance una orden)

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

### Estado de la verificación

- **Claude Code**: conectado a producción mediante Streamable HTTP con un
  token personal, según informó el responsable del mantenimiento
  (2026-10-06).
- **Codex 0.157, OpenCode 1.18, Pi 1.0, OpenClaw 2026.7 y Claude Code**: los
  fragmentos anteriores (con una URL local) se conectaron a un servidor local
  construido a partir de `main@69bcfa7` y llamaron a `qms_modules` con un
  token de solo lectura, cada uno mediante su orden habitual de ejecución
  puntual; el registro del servidor anotó las llamadas (2026-10-06). El
  marcador de posición `${...}` o `{env:...}` de cada cliente se expandió
  desde el entorno.
- **Claude Desktop mediante `mcp-remote`**: sin probar (no hay versión para
  Linux); las opciones `--header` / `--header-file` siguen la documentación de
  `mcp-remote` v0.14.3.

El servidor no tiene estado: no envía `Mcp-Session-Id` y responde con JSON.

## Prueba de humo

Para cada cliente, después de configurarlo con un token `read` de un usuario
que pueda leer no conformidades:

1. **Claude Code:** `claude mcp list` muestra `iso9001` conectado; pídale que
   ejecute `qms_modules` y que después enumere las no conformidades.
2. **Codex:** `codex mcp list` muestra el servidor; pídale que llame a
   `qms_modules`.
3. **Pi:** abra el panel MCP (`/mcp`) y compruebe las cinco herramientas
   `qms_*`; llame a `qms_modules`.
4. **OpenCode:** `opencode mcp list` muestra `iso9001` conectado; llame a
   `qms_modules`.
5. **OpenClaw:** compruebe que el servidor aparece con las cinco herramientas;
   llame a `qms_modules`.
6. **Claude Desktop:** reinícielo, compruebe que el icono de herramientas
   incluye `iso9001` y pídale los módulos.

A continuación, una sola vez, con un token `write` de un usuario de prueba:
cree una no conformidad, léala con `qms_get` y confirme que la fila de
auditoría tiene el canal `mcp`. Un token incorrecto debe dar `401` (por
ejemplo, `curl -i -X POST https://qms.example.com/mcp` sin cabecera).
