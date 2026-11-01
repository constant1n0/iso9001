# SGC ISO 9001

[English summary](README.en.md)

**Sistema de gestión de la calidad para ISO 9001:2026, auditable de principio
a fin, en el que las personas y los agentes de IA trabajan sobre los mismos
registros.**

Esta aplicación web reúne los registros de un sistema de gestión de la calidad
(SGC): no conformidades y acciones correctivas, control de documentos,
auditorías internas, objetivos de la calidad, revisión por la dirección,
competencia del personal y el resto de registros de la norma. Está pensada
para empresas y organizaciones que implantan o mantienen un SGC conforme a ISO
9001:2026 y necesitan demostrar ante un auditor quién hizo cada cosa, cuándo y
con qué resultado.

La interfaz está en español. Los agentes de IA (Claude Code, Codex y otros
clientes MCP) consultan y registran datos en el mismo SGC a través de un
servidor MCP, con los permisos del usuario al que representan y sin poder
eliminar nada.

<!-- capturas: pendiente de añadir capturas del panel de control, la ficha de
una no conformidad, el flujo de revisiones de un documento, el acta de la
revisión por la dirección en PDF y un agente de IA consultando el SGC por MCP.
No enlazar imágenes hasta que existan en el repositorio. -->

## Qué resuelve

- **Trazabilidad lista para la auditoría.** Cada cambio pasa por una única
  capa de servicios que comprueba los permisos, valida los datos y escribe el
  registro de auditoría: quién, cuándo, por qué canal y los valores antes y
  después del cambio. Los usuarios se desactivan y los documentos se dan de
  baja; ninguno se elimina, y las revisiones sustituidas de un documento se
  conservan como evidencia.
- **Las reglas de la norma, aplicadas por el sistema.** Tres roles
  (Administrador, Auditor y Operativo) deciden qué puede consultar, crear,
  editar, eliminar o aprobar cada cuenta. Cada registro sigue sus estados: un
  documento se revisa, se aprueba y se publica; la eficacia de una acción
  correctiva se verifica; una revisión por la dirección se celebra y sus
  entradas quedan congeladas.
- **Agentes de IA bajo control.** Un administrador emite a cada agente un
  token de API revocable que actúa como un usuario concreto y solo puede
  reducir los permisos de su rol. Lo que registra un agente queda en el
  registro de auditoría con el nombre de ese usuario. Un agente nunca elimina,
  nunca escribe documentos y nunca gestiona tokens; verificar acciones
  correctivas, cerrar no conformidades, aprobar programas de auditoría y
  celebrar revisiones por la dirección y responder, cerrar, cancelar o reabrir
  reclamaciones solo se hace desde la web.
- **Evidencia en PDF y avisos por correo electrónico.** Informes en PDF de las
  no conformidades, las auditorías y sus programas, los objetivos de la
  calidad, las capacitaciones, las encuestas de satisfacción y las actas de la
  revisión por la dirección; un informe mensual de calidad que los
  administradores reciben el día 1 de cada mes, y avisos programados de
  auditorías próximas y de revisiones de documentos pendientes.
- **Software libre, en su propio servidor.** Licencia GPL v3. Funciona con
  PostgreSQL, Redis, Celery y Gunicorn detrás de un proxy inverso, y la
  interfaz no carga recursos de terceros.

## Módulos

| Módulo | Apartado de ISO 9001:2026 | Qué cubre |
|---|---|---|
| Panel de control | — | Cifras, gráficos y avisos, entre ellos las revisiones de documentos vencidas, los objetivos que no alcanzan la meta y las acciones vencidas de la revisión por la dirección |
| Usuarios y perfil | 5.3 | Cuentas con los roles Administrador, Auditor y Operativo, desactivación en lugar de eliminación, y el perfil y los tokens de API de cada usuario |
| Contexto, partes interesadas y alcance | 4.1, 4.2, 4.3 | Cuestiones externas e internas con su pertinencia y la del cambio climático, que citan los riesgos; partes interesadas con tipo, pertinencia, responsable, próxima revisión y requisitos climáticos; alcance del SGC por versiones aprobadas, con la aplicabilidad justificada de cada requisito y su PDF |
| Procesos | 4.4, 8.1 | Tipo, responsable, entradas, salidas, recursos, criterios de operación y de aceptación, procesos relacionados y los registros que citan cada proceso |
| Personas y competencia | 5.3, 7.2 | Personas, roles del SGC con sus responsabilidades, autoridades y línea de reporte, competencias requeridas y acreditadas, y la matriz de competencias |
| No conformidades y acciones correctivas | 8.7, 10.2 | Tratamiento de las salidas no conformes, concesión con autoridad y fecha, proceso vinculado, contención, causa raíz, acciones correctivas, verificación de la eficacia, cierre y reapertura |
| Confirmaciones de lectura | 5.2, 7.3 | Persona, fecha no futura y revisión concreta de un documento o confirmación de los objetivos; historial en la ficha de la persona, pantallas y MCP |
| Control de documentos | 7.5 | Revisiones numeradas que se revisan, aprueban y publican, ficheros adjuntos, baja y revisión periódica |
| Auditorías internas | 9.2 | Programa anual de auditoría, auditorías con alcance, equipo y comprobación de independencia, y hallazgos clasificados que abren no conformidades |
| Riesgos y oportunidades | 6.1 | Registros separados de riesgos y de oportunidades con valoración, valoración residual, mapa de calor, acciones con la evaluación de su eficacia, revisiones vencidas y PDF |
| Objetivos de la calidad | 6.2 | Indicador, meta, responsable, mediciones, tendencia y mediciones vencidas |
| Cambios en el SGC | 6.3 | Cambios planificados con su origen, las consideraciones a) a d), la comunicación, los procesos y documentos afectados, tareas, aprobación por un administrador, implantación, revisión de la eficacia, avisos en el panel, PDF del registro y del plan, y entrada de la revisión por la dirección |
| Proveedores externos | 8.4, 9.1.3 | Criterios de selección, procesos afectados, aprobación por un administrador, evaluaciones puntuadas con la próxima evaluación, no conformidades vinculadas y PDF de proveedores aprobados |
| Clientes, reclamaciones y satisfacción | 8.2, 9.1.2 | Registro de clientes con NIF y contacto; reclamaciones con respuesta, cierre, cancelación y reapertura, vínculo opcional a una no conformidad; encuestas vinculadas al cliente, con tipo y canal |
| Revisión por la dirección | 9.3 | Entradas tomadas de los registros, conclusiones, acciones y acta en PDF |
| Mejoras | 10.1, 10.3 | Origen y registro vinculado, responsable, beneficio esperado, aprobación, fecha real de implantación y revisión independiente de la eficacia; transiciones solo desde la web |
| Informes y notificaciones | 9.1 | PDF de los registros, informe mensual de calidad y avisos programados por correo electrónico |

**Otros registros**, anteriores a los módulos de la tabla: recursos de
capacitación (7.1), capacitaciones (7.2) e indicadores de auditoría (9.1).
Todos tienen pantallas.

La [guía de usuario](docs/guia-de-usuario.md) explica cada pantalla, los
permisos de cada rol y las reglas de cada módulo.

## Agentes de IA

El servidor MCP (`app/mcp_server/`) permite que los agentes de IA lean y
escriban en el SGC como un usuario, con las mismas reglas que la web. Su
configuración está documentada para Claude Code, Codex, Pi, OpenCode, OpenClaw
y Claude Desktop.

Los administradores emiten los tokens de API desde una línea de órdenes en el
servidor con `flask --app run.py create-api-token`, los enumeran con
`list-api-tokens` y los revocan con `revoke-api-token` (véase la
[guía de operaciones](docs/operaciones.md#tokens-de-api)). El servidor MCP, su despliegue y
la configuración de cada cliente están en [`docs/mcp.md`](docs/mcp.md).

## Puesta en marcha (desarrollo)

Se necesita Python 3.11, una base de datos PostgreSQL (Docker es la forma más
rápida) y las bibliotecas del sistema que WeasyPrint usa para los PDF (véanse
los requisitos en la [guía de operaciones](docs/operaciones.md#requisitos)).

```bash
git clone https://github.com/constant1n0/iso9001.git
cd iso9001
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt

docker run -d --name iso9001-dev-db -e POSTGRES_PASSWORD=<contraseña> \
  -p 127.0.0.1:5432:5432 postgres:17-alpine

cat > .env <<'EOF'
SECRET_KEY=<cadena-aleatoria>
DATABASE_URI=postgresql://postgres:<contraseña>@127.0.0.1:5432/postgres
FLASK_DEBUG=1
EOF

venv/bin/flask --app run.py db upgrade
venv/bin/flask --app run.py create-admin   # el primer administrador, una sola vez
venv/bin/flask --app run.py run
```

Abra `http://127.0.0.1:5000` e inicie sesión con ese administrador. Celery, el
correo electrónico, la configuración de producción y todas las variables de
entorno están en la [guía de operaciones](docs/operaciones.md#configuración).

## Documentación

| Documento | Contenido |
|---|---|
| [Guía de usuario](docs/guia-de-usuario.md) | La guía principal: cada módulo, los roles, los flujos, los PDF y los correos |
| [User guide](docs/user-guide.md) | La traducción al inglés de la guía de usuario |
| [Guía de operaciones](docs/operaciones.md) | Instalación, configuración, PostgreSQL, Redis y Celery, Gunicorn, el proxy inverso y systemd, despliegue, copias de seguridad, monitorización, seguridad e integración continua |
| [Servidor MCP](docs/mcp.md) | Tokens, el servidor MCP y la configuración de los clientes de agentes de IA |
| [Análisis de brechas ISO 9001](docs/analisis-brechas-iso-9001.md) | La cobertura de cada apartado de la norma |
| [Capa de servicios](docs/architecture/services.md) | Arquitectura, matriz de permisos, reglas de estado y cómo escribir un adaptador (en inglés, para desarrolladores) |

Los seguimientos de `odd/tasks/` (en inglés) recogen las decisiones, las
comprobaciones y los despliegues en producción de cada cambio.

## Desarrollo

- **Estructura**: `app/` contiene las rutas, los servicios, los modelos, las
  plantillas, los recursos estáticos y el servidor MCP; `migrations/`, las
  migraciones de Alembic; `tests/`, las pruebas; `celery_worker.py`, las
  tareas programadas; `wsgi.py` y `gunicorn.conf.py`, el punto de entrada de
  producción, y los ficheros `iso9001*.service`, las unidades de systemd.
- **Arquitectura**: las escrituras pasan por una capa de servicios
  independiente de Flask (permisos, validación, atribución y registro de
  auditoría); la web y el servidor MCP son adaptadores sobre ella. Véase
  [`docs/architecture/services.md`](docs/architecture/services.md).
- **Pruebas**: `venv/bin/python -m unittest discover -s tests -p 'test_*.py'`.
  Las pruebas de PostgreSQL solo se ejecutan con `TEST_POSTGRES_URI`; véase la
  integración continua en la [guía de operaciones](docs/operaciones.md#integración-continua).
- **Interfaz**: una sola hoja de estilos, `app/static/css/app.css`, con los
  tokens de diseño (negro, blanco hueso y amarillo canario `#FFF000`) como
  propiedades personalizadas de CSS. Todos los recursos se sirven desde el
  propio servidor, de modo que la Content-Security-Policy no admite orígenes
  externos: las tipografías Barlow Condensed, IBM Plex Sans e IBM Plex Mono
  (SIL Open Font License, véase `app/static/fonts/*-OFL.txt`) y Chart.js 4.5.1
  (MIT, véase `app/static/lib/chart.js-LICENSE.md`). Las piezas de plantilla
  compartidas están en `app/templates/_partials/`. Los formularios marcados
  con `data-confirm="…"` piden confirmación a través de `app/static/js/app.js`;
  no se usan manejadores `onclick` ni `onsubmit` en línea.

## Integración continua

GitHub Actions ejecuta la comprobación `Python 3.11 tests`, contra PostgreSQL
17, en cada *push*, en las *pull requests* dirigidas a `main` y cuando se
lanza manualmente. Los detalles están en la
[guía de operaciones](docs/operaciones.md#integración-continua).

## Licencia

GNU General Public License v3.0; véase [LICENSE](LICENSE).
