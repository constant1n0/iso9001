# Guía de usuario

Versión en inglés: [User guide](user-guide.md).

Esta guía está dirigida a las personas que usan el SGC ISO 9001 desde un
navegador web: personal de calidad, responsables y auditores. Explica cada
pantalla, lo que se puede hacer en ella según el rol y las reglas que aplica
el sistema. Abarca los requisitos del sistema de gestión de la calidad de ISO
9001:2026 que la aplicación registra.

La guía cita los textos de la interfaz exactamente, en negrita: por ejemplo,
**No conformidades**. La sección [Términos](#términos), al final, explica
algunos términos con un significado propio en la aplicación.

Documentación técnica:

- la instalación y la administración del servidor, en la
  [guía de operaciones](operaciones.md);
- el servidor MCP para agentes de IA, en la [guía del servidor MCP](mcp.md);
- la arquitectura, la matriz de permisos y las reglas de estado, en la
  [capa de servicios](architecture/services.md) (en inglés, para
  desarrolladores).

## Contenido

- [Inicio de sesión y cuenta](#inicio-de-sesión-y-cuenta)
- [Uso de las pantallas](#uso-de-las-pantallas)
- [Roles y acceso](#roles-y-acceso)
- [Panel de control](#panel-de-control)
- [Usuarios](#usuarios)
- [Personas y competencia](#personas-y-competencia)
- [No conformidades y acciones correctivas](#no-conformidades-y-acciones-correctivas)
- [Control de documentos](#control-de-documentos)
- [Programa de auditoría, auditorías y hallazgos](#programa-de-auditoría-auditorías-y-hallazgos)
- [Contexto de la organización](#contexto-de-la-organización)
- [Partes interesadas](#partes-interesadas)
- [Alcance del SGC](#alcance-del-sgc)
- [Procesos](#procesos)
- [Riesgos y oportunidades](#riesgos-y-oportunidades)
- [Objetivos de la calidad](#objetivos-de-la-calidad)
- [Cambios en el SGC](#cambios-en-el-sgc)
- [Proveedores externos](#proveedores-externos)
- [Revisión por la dirección](#revisión-por-la-dirección)
- [Otros registros](#otros-registros)
- [Informes y notificaciones por correo electrónico](#informes-y-notificaciones-por-correo-electrónico)
- [Términos](#términos)

## Inicio de sesión y cuenta

### Inicio de sesión

1. Abra la dirección de la aplicación. Aparece la página **Acceso**.
2. Escriba su **Usuario** y su **Contraseña** y pulse **Entrar**.
3. Se abre el **Panel de control**.

Las credenciales erróneas y las cuentas que un administrador ha desactivado
reciben el mismo mensaje: **Credenciales inválidas**. Tras cinco intentos en
un minuto desde el mismo lugar, los intentos siguientes se rechazan durante un
tiempo.

No hay registro público: un administrador crea las cuentas (véase
[Usuarios](#usuarios)). Para salir, pulse **Salir** en la barra superior. La
sesión también termina sola al cerrar el navegador, tras ocho horas sin
actividad y, aunque siga trabajando, doce horas después de iniciarla; la
página de acceso lo indica con un aviso y basta con volver a iniciar sesión.
En un equipo compartido, pulse **Salir** o cierre el navegador al terminar.

### Contraseña olvidada

1. En la página de acceso, siga el enlace **¿Has olvidado tu contraseña?**.
2. En **Recuperar contraseña**, escriba la dirección de correo electrónico de
   su cuenta y pulse **Enviar enlace**.
3. La página responde siempre que se ha enviado un correo, tanto si la
   dirección pertenece a una cuenta activa como si no, para que nadie pueda
   usarla para descubrir cuentas.
4. Abra el enlace del correo. Caduca al cabo de una hora y deja de funcionar
   en cuanto cambia la contraseña.
5. En **Nueva contraseña**, escriba dos veces la nueva contraseña (al menos 8
   caracteres) y pulse **Guardar contraseña**. Después, inicie sesión con ella.

Un administrador también puede enviarle el enlace desde la lista de usuarios
(véase [Usuarios](#usuarios)).

### Su perfil

**Mi perfil** (`/perfil/`) está abierto a todos los usuarios. Se llega desde
la última opción del menú o haciendo clic en el nombre de usuario de la barra
superior.

- **Datos de la cuenta**: el nombre de usuario, la dirección de correo
  electrónico, el rol y el estado de la cuenta. Solo un administrador cambia
  el rol.
- **Cambiar correo electrónico**: escriba el **Nuevo correo electrónico** y la
  **Contraseña actual** y pulse **Cambiar correo**. Se rechaza una dirección
  que ya usa otra cuenta.
- **Cambiar contraseña**: escriba la **Contraseña actual**, la **Nueva
  contraseña** (al menos 8 caracteres y distinta de la actual) y **Confirmar
  nueva contraseña**, y pulse **Cambiar contraseña**.
- Los dos formularios comparten un límite de diez intentos por hora para cada
  cuenta. Una contraseña actual errónea muestra **La contraseña actual no es
  correcta.**
- **Mis tokens de API**: los tokens emitidos a su nombre para agentes de IA,
  con su **Nombre**, **Prefijo**, **Permisos**, **Estado** (**Activo**,
  **Caducado**, **Revocado**), **Caduca** y **Último uso**. **Revocar**
  invalida un token al instante. El secreto no se vuelve a mostrar después de
  emitirlo; los tokens se emiten desde la línea de órdenes del servidor (véase
  [Agentes y tokens de API](#agentes-y-tokens-de-api)).

## Uso de las pantallas

### Navegación

El menú de la izquierda agrupa los módulos. Cada opción muestra el apartado
de ISO 9001:2026 al que da soporte. Solo aparecen las opciones que su rol
puede consultar.

| Grupo | Opciones |
|---|---|
| (arriba) | **Panel** |
| **Contexto** | **Contexto** 4.1, **Partes interesadas** 4.2, **Alcance del SGC** 4.3 |
| **Calidad** | **Procesos** 4.4, **Riesgos** 6.1, **Oportunidades** 6.1, **Objetivos de calidad** 6.2, **Indicadores de auditoría** 9.1, **Auditorías** 9.2, **Programas de auditoría** 9.2.2, **Revisión por la dirección** 9.3, **No conformidades** 10.2, **Mejoras** 10.3 |
| **Clientes** | **Satisfacción** 9.1.2 |
| **Personas y competencia** | **Roles y responsabilidades** 5.3, **Personas** 7.2, **Competencias requeridas** 7.2, **Matriz de competencias** 7.2, **Capacitaciones** 7.2, **Recursos de capacitación** 7.1 |
| **Documentación** | **Documentos** 7.5 |
| **Administración** | **Usuarios** 5.3 |
| **Cuenta** | **Mi perfil** |

En una pantalla estrecha, el menú se abre con el botón situado a la izquierda
de la barra superior.

### Convenciones

- **Listas.** Las listas tienen un panel **Filtros**: indique los valores y
  pulse **Buscar**; **Limpiar** los quita. Un valor de filtro que no existe se
  ignora.
- **Los botones solo aparecen cuando se pueden usar.** El rol del usuario y el
  estado del registro deciden qué botones muestra cada página.
- **Confirmaciones.** Eliminar, y la mayoría de los cambios de estado, piden
  confirmación previa.
- **Mensajes.** Al guardar, un mensaje verde lo confirma. Cuando el sistema
  rechaza una acción, un mensaje rojo explica el motivo (esta guía cita los
  textos) y el formulario vuelve con lo que se había escrito. Abrir una página
  que su rol no puede usar muestra **No tienes permiso para acceder a esta
  página.** y devuelve al panel de control.
- **Fechas.** Se muestran como día/mes/año. «Hoy» es la fecha en la zona
  horaria de la organización, configurada en el servidor.
- **Selectores de personas.** Muchos formularios eligen una persona de
  **Personas** (véase [Personas y competencia](#personas-y-competencia)). Solo
  se ofrecen las personas activas; un registro conserva a una persona aunque
  se desactive después.
- **Su propia persona.** Cuando su cuenta de usuario está vinculada a una
  persona, algunos formularios empiezan con esa persona seleccionada (el autor
  de un documento nuevo, quien registra una medición, quien aprueba un
  programa de auditoría), y el sistema la registra cuando usted celebra una
  revisión por la dirección, aprueba el alcance del SGC o da de baja un
  documento. Pida a un administrador
  o a un auditor que vincule su cuenta con su persona.
- **Nombres heredados.** Los registros antiguos de capacitaciones, no
  conformidades y auditorías guardan un nombre en texto libre (**Personal**,
  **Responsable**, **Auditor**) junto al selector de persona. Los textos
  existentes se conservan y nunca se asocian a una persona. Si deja el texto
  en blanco y elige una persona, el texto toma el nombre de esa persona. El
  texto solo es obligatorio cuando no se elige ninguna persona.
- **Los PDF** se abren en una pestaña nueva del navegador.

## Roles y acceso

Cada cuenta tiene uno de estos tres roles: **Administrador**, **Auditor** u
**Operativo**. El rol decide qué se puede consultar, crear, editar, eliminar y
aprobar en cada módulo.

| Módulo | Administrador | Auditor | Operativo |
|---|---|---|---|
| Usuarios | Consultar, crear, editar, desactivar, reactivar, enviar enlaces de restablecimiento | Consultar | Sin acceso |
| Perfil propio | Todo | Todo | Todo |
| Personas | Consultar, crear, editar, eliminar | Consultar, crear, editar | Consultar |
| Competencia (requisitos, competencias acreditadas, matriz) | Consultar, crear, editar, eliminar | Consultar, crear, editar | Consultar |
| Capacitaciones | Consultar, crear, editar, eliminar | Consultar, crear, editar | Consultar, crear, editar |
| No conformidades | Consultar, crear, editar, eliminar; cancelar, cerrar, reabrir | Consultar, crear, editar; cancelar, cerrar | Consultar, crear, editar |
| Acciones correctivas | Consultar, crear, editar, eliminar; verificar | Consultar, crear, editar; verificar | Consultar, crear, editar |
| Documentos | Consultar todas las revisiones, crear, editar, enviar a revisión, rechazar, publicar, adjuntar ficheros; aprobar, dar de baja | Consultar todas las revisiones, crear, editar, enviar a revisión, rechazar, publicar, adjuntar ficheros | Consultar la revisión vigente |
| Cuestiones del contexto | Consultar, crear, editar, eliminar | Consultar, crear, editar | Consultar |
| Alcance del SGC | Consultar; crear, editar y eliminar borradores; aprobar | Consultar; crear, editar y eliminar borradores | Consultar |
| Programas de auditoría, auditorías y hallazgos | Consultar, crear, editar, eliminar; aprobar programas | Consultar, crear, editar | Sin acceso |
| Riesgos, oportunidades y sus acciones | Consultar, crear, editar, eliminar; evaluar la eficacia de las acciones | Consultar, crear, editar; evaluar la eficacia de las acciones | Consultar |
| Objetivos de la calidad y mediciones | Consultar, crear, editar, cambiar el estado, eliminar | Consultar, crear, editar, cambiar el estado | Consultar |
| Cambios en el SGC y sus tareas | Consultar, crear, editar, eliminar; aprobar, cancelar, registrar la implantación, revisar la eficacia | Consultar, crear, editar; registrar la implantación, revisar la eficacia | Consultar |
| Proveedores externos y sus evaluaciones | Consultar, crear, editar, eliminar; aprobar, condicionar, bloquear | Consultar, crear, editar | Consultar |
| Revisiones por la dirección y sus acciones | Consultar, crear, editar, eliminar; celebrar, cerrar | Consultar, crear, editar | Sin acceso |
| Mejoras, satisfacción del cliente, partes interesadas | Consultar, crear, editar, eliminar | Consultar, crear, editar | Consultar, crear, editar |
| Procesos, roles, recursos de capacitación, indicadores de auditoría | Consultar, crear, editar, eliminar | Consultar, crear, editar | Consultar |

- Nadie elimina usuarios ni documentos: los usuarios se desactivan y los
  documentos se dan de baja, de modo que el historial queda completo.
- Otras reglas dependen del estado del registro; cada sección de módulo las
  enumera más abajo.
- Cada cambio queda registrado en el registro de auditoría con el usuario que
  lo hizo.

### Agentes y tokens de API

Los agentes de IA (por ejemplo, Claude Code o Codex) trabajan con el SGC a
través de su servidor MCP. Un administrador emite a cada agente un token de
API desde la línea de órdenes del servidor (véase
[Tokens de API](operaciones.md#tokens-de-api) en la guía de operaciones).

- Un token actúa como el usuario al que se emitió, con el rol de ese usuario.
  Sus permisos solo pueden reducir los derechos del rol: un token `read` solo
  consulta, y un token `write` nunca llega a un módulo en el que el rol del
  usuario no puede escribir.
- Un agente nunca elimina, nunca escribe documentos y nunca gestiona tokens.
  Verificar acciones correctivas; cerrar, cancelar y reabrir no
  conformidades; aprobar programas de auditoría; celebrar y cerrar revisiones
  por la dirección; aprobar, condicionar y bloquear proveedores; aprobar el
  alcance del SGC; aprobar, implantar, revisar la eficacia y cancelar cambios
  en el SGC, y los PDF, solo se hacen desde la interfaz web.
- Lo que guarda un agente lleva el nombre de su usuario en el registro de
  auditoría, igual que un cambio hecho desde la web.
- Cada usuario ve y revoca sus propios tokens en **Mi perfil**; desactivar un
  usuario revoca sus tokens activos.

Los módulos, los campos y la configuración de los clientes están en la
[guía del servidor MCP](mcp.md).

## Panel de control

El **Panel de control** (`/dashboard/`) se abre al iniciar sesión. Muestra el
estado del SGC a todos los roles. La cifra **Auditorías** y la tarjeta
**Auditorías próximas** solo las ven los roles que pueden consultar las
auditorías (administradores y auditores); un usuario con rol Operativo no ve
ninguna cifra, área ni fecha de auditoría.

**Botones de acceso rápido**, en la parte superior, para crear registros:
**+ Auditoría** (administradores y auditores), **+ No conformidad**,
**+ Capacitación** y **+ Encuesta** (encuesta de satisfacción del cliente).

**Cifras:**

| Cifra | Qué cuenta |
|---|---|
| **Auditorías** | Todas las auditorías registradas, en cualquier estado; debajo, cuántas quedan por celebrar en los próximos 7 días |
| **No conformidades abiertas** | Las no conformidades abiertas (ni cerradas ni canceladas) sobre el total; debajo, las cerradas |
| **Satisfacción media** | La puntuación media de todas las encuestas de satisfacción, sobre 10 |
| **Capacitaciones** | Todas las sesiones de capacitación registradas; debajo, cuántas tienen fecha en los próximos 30 días |

**Tarjetas de aviso:**

- **Auditorías próximas**, para administradores y auditores: las auditorías
  que quedan por celebrar (ni completadas ni canceladas) con fecha de hoy o de
  los próximos 7 días, de la más próxima a la más lejana, con su área y su
  fecha.
- **No conformidades abiertas**: las no conformidades abiertas, de la
  detectada hace más tiempo a la más reciente, con su responsable o **Sin
  asignar**.
- **Capacitaciones próximas**: las sesiones de capacitación con fecha en los
  próximos 30 días.
- **Revisión de documentos**: los documentos en uso cuya revisión periódica
  está vencida o vence en los próximos 30 días, con la etiqueta **Vencida** en
  las vencidas. Un usuario con rol Operativo solo ve los documentos vigentes.
  Cuando hay revisiones vencidas, un enlace abre la lista de documentos
  filtrada por ellas.
- **Objetivos de calidad**: los objetivos de la calidad activos que no
  alcanzan su meta (**No cumple la meta**) o tienen una medición vencida
  (**Medición vencida**). Cuando los hay, un enlace muestra la lista de
  objetivos activos.
- **Riesgos y oportunidades**: cuántos registros abiertos requieren atención,
  cada cifra con un enlace a la lista filtrada por ellos: **Riesgos altos o
  críticos sin acción abierta** (sin ninguna acción pendiente ni en curso),
  **Riesgos con la revisión vencida**, **Oportunidades con la revisión
  vencida**, **Riesgos sin valorar** y **Oportunidades sin valorar**. Solo
  aparecen las cifras que no son cero; sin ninguna, la tarjeta muestra
  **Ningún riesgo ni oportunidad requiere atención.**
- **Contexto**: cuántas cuestiones del contexto vigentes y cuántas partes
  interesadas tienen la revisión vencida, cada cifra con un enlace a la lista
  filtrada por ellas: **Cuestiones del contexto con la revisión vencida** y
  **Partes interesadas con la revisión vencida**. Solo aparecen las cifras que
  no son cero; sin ninguna, la tarjeta muestra **Ninguna cuestión ni parte
  interesada tiene la revisión vencida.**
- **Acciones de la revisión por la dirección**, para administradores y
  auditores: las acciones vencidas, con su responsable y su fecha límite.
  Cuando las hay, un enlace abre el registro de acciones filtrado por ellas.
- **Proveedores**: cuántos proveedores requieren atención, cada cifra con un
  enlace a la lista filtrada por ellos: **Proveedores con la evaluación
  vencida**, **Proveedores bloqueados con procesos** (bloqueados pero todavía
  vinculados a algún proceso) y **Proveedores aprobados con resultado «No
  apto»** (en su última evaluación). Solo aparecen las cifras que no son cero;
  sin ninguna, la tarjeta muestra **Ningún proveedor requiere atención.**
- **Cambios en el SGC**: cuántos cambios requieren atención, cada cifra con
  un enlace a la lista de cambios filtrada por ellos: **Cambios pendientes de
  aprobación** (propuestos), **Cambios aprobados con la implantación vencida**
  (fuera de plazo), **Cambios con tareas vencidas** (cada cambio cuenta una
  vez, aunque tenga varias tareas vencidas) y **Cambios implantados sin
  revisar su eficacia**. Solo aparecen las cifras que no son cero; sin
  ninguna, la tarjeta muestra **Ningún cambio requiere atención.**

**Gráficos:**

- **No conformidades**: no conformidades abiertas frente a cerradas.
- **Satisfacción del cliente**: la puntuación media de las encuestas de cada
  mes, en los últimos 12 meses.

## Usuarios

**Usuarios** (`/usuarios/`, menú **Administración**) gestiona las cuentas que
inician sesión (apartado 5.3 de ISO 9001:2026). Los administradores las
gestionan; los auditores ven la lista en modo de solo lectura; los usuarios
con rol Operativo no pueden abrirla.

La lista muestra el **Usuario**, el **Correo electrónico**, el **Rol** y el
**Estado** (**Activo** o **Inactivo**) de cada cuenta.

**Para crear un usuario**, pulse **+ Nuevo usuario** y rellene:

- **Nombre de usuario**: de 4 a 150 caracteres, único; no cambia nunca
  después;
- **Correo electrónico**: único, sin distinguir mayúsculas de minúsculas;
- **Rol**: **Administrador**, **Auditor** u **Operativo** (el valor por
  defecto);
- **Contraseña** y **Confirmar contraseña**: al menos 8 caracteres.

Pulse **Crear usuario**.

**Para cambiar el correo electrónico o el rol**, pulse **Editar** en la lista.

**Acciones sobre la cuenta** (en la lista y en la página de edición):

- **Enviar enlace de contraseña** envía al usuario por correo un enlace de
  restablecimiento válido durante una hora. Requiere una cuenta activa con
  dirección de correo electrónico. Cada administrador envía como máximo diez
  enlaces por hora, desde cualquier dirección de red.
- **Desactivar**: el usuario ya no puede iniciar sesión, pierde cualquier
  sesión abierta y sus tokens de API activos se revocan. El registro de
  auditoría conserva su nombre.
- **Reactivar** le devuelve el acceso.

**Rechazos:**

- **No puedes cambiar tu propio rol.** y **No puedes desactivar tu propia
  cuenta.**
- **No se puede desactivar ni retirar el rol al último administrador activo.**
  Siempre queda al menos un administrador activo.
- **Ya existe un usuario con ese nombre de usuario.** y **Ya existe un
  usuario con ese correo electrónico.**

Los cambios en las cuentas se anotan también en el registro de seguridad.

## Personas y competencia

El grupo **Personas y competencia** registra quién trabaja bajo el SGC, qué
roles desempeña, la competencia que exige cada rol y la competencia que cada
persona ha acreditado (apartados 5.3 y 7.2 de ISO 9001:2026). Todos los roles
consultan las personas y la competencia; los administradores y los auditores
crean y editan; solo los administradores eliminan. **Capacitaciones** y
**Recursos de capacitación**, en el mismo grupo del menú, se describen en
[Otros registros](#otros-registros).

### Roles y responsabilidades

**Roles y responsabilidades** (`/roles/`) son los roles del SGC, con sus
responsabilidades y sus autoridades (apartado 5.3). Las personas eligen sus
roles en **Personas**, y cada requisito de **Competencias requeridas** cita un
rol. Para añadir un rol, pulse **+ Nuevo rol** y rellene:

- **Nombre** (obligatorio y único: **Ya existe un rol con ese nombre.**);
- **Reporta a**: el rol del que depende, o **— No reporta a ningún rol —**;
- **Responsabilidades** y **Autoridades**;
- **Compromiso con la calidad** y **Política de calidad**.

**Reglas:**

- Un rol no puede reportar a sí mismo, y el selector no lo ofrece. Tampoco
  puede reportar a un rol que ya le reporta, directa o indirectamente: **La
  línea de reporte no puede formar un ciclo: ese rol ya reporta, directa o
  indirectamente, a este.**
- No se puede eliminar un rol que citan requisitos de competencia: **No se
  puede eliminar un rol que citan requisitos de competencia; elimínalos o
  asígnalos a otro rol antes.**
- No se puede eliminar un rol al que reportan otros roles: **No se puede
  eliminar un rol al que reportan otros roles; cambia antes a quién
  reportan.** Las personas que desempeñan un rol eliminado simplemente lo
  pierden.

**Pantallas:**

- **La lista**, por nombre, muestra a quién reporta cada rol, cuántas personas
  lo desempeñan (también las inactivas) y su compromiso con la calidad.
- **La página de un rol** muestra sus textos; su **Línea de reporte**, desde
  el rol del que depende directamente hasta la cúspide; los **Roles que le
  reportan** directamente; las **Personas que lo desempeñan**, y su
  **Competencia requerida**, con el enlace **Ver en competencias**.
- Cuando un rol tiene requisitos de competencia o roles que le reportan, su
  página no muestra **Eliminar**.

### Personas

**Personas** (`/personas/`) son las personas que realizan trabajos bajo el
SGC. Son independientes de las cuentas de usuario: una persona puede no tener
cuenta.

- **Campos**: **Nombre completo**, **Correo electrónico** (opcional, único
  entre las personas), **Roles** (cualquier número de roles del SGC),
  **Usuario vinculado** (cuenta de usuario vinculada; como máximo una persona
  por usuario), **Activa** y **Notas**.
- **Filtros de la lista**: **Nombre**, **Estado** (**Todas**, **Activas**,
  **Inactivas**) y **Rol**.
- **La página de una persona** muestra sus datos, sus roles y las
  **Competencias acreditadas**.
- **Eliminación**: no se puede eliminar una persona que citan otros registros
  (una no conformidad, una acción, una auditoría, un documento, una revisión,
  un proceso del que es responsable, etc.): **No se puede eliminar una persona
  citada en otros registros; desactívala en su lugar.** En su lugar, desmarque
  **Activa**.

### Competencias requeridas

**Competencias requeridas** (`/competencias/requisitos/`) enumera lo que exige
cada rol. Para añadir un requisito, pulse **+ Nuevo requisito** y rellene:

- **Rol**;
- **Tipo**: **Educación**, **Formación**, **Habilidad** o **Experiencia**;
- **Descripción**;
- **Criterio para evidenciarla**.

La lista se filtra por rol. No se puede eliminar un requisito que citan
competencias acreditadas.

### Competencias acreditadas

En la página de una persona, **+ Añadir competencia** registra una evidencia:

- **Requisito de competencia** (opcional; solo los registros vinculados a un
  requisito cuentan en la matriz);
- **Evidencia** (un texto o una referencia);
- **Capacitación** (opcional; una sesión de capacitación como evidencia);
- **Fecha de obtención** y **Fecha de caducidad** (opcional);
- **Evaluación de la eficacia**: **Pendiente**, **Eficaz** o **No eficaz**;
- **Fecha de evaluación** y **Evaluador** (la persona que evalúa).

**Reglas:**

- La fecha de caducidad no puede ser anterior a la fecha de obtención.
- Una evaluación distinta de pendiente necesita la fecha de evaluación y el
  evaluador: **Una evaluación de eficacia distinta de «pendiente» necesita la
  fecha de evaluación y el evaluador.**
- La fecha de evaluación no puede ser anterior a la fecha de obtención.

### Matriz de competencias

**Matriz de competencias** (`/competencias/matriz`) muestra, para cada rol con
requisitos, sus personas activas (filas) frente a cada requisito (columnas),
evaluadas a fecha de hoy. Se puede filtrar por **Rol**. Un registro sin fecha
de caducidad no caduca nunca, y uno que caduca hoy sigue siendo válido. Cada
celda enlaza con la página de la persona y muestra uno de estos estados:

| Estado | Significado |
|---|---|
| **Cumplida** | Hay un registro sin caducar evaluado como eficaz, aunque un intento posterior no lo fuera |
| **Pendiente de evaluación** | Si no, el registro sin caducar más reciente sigue pendiente |
| **No eficaz** | Si no, el registro sin caducar más reciente no fue eficaz |
| **Caducada** | Todos los registros han caducado |
| **Falta** | No hay ningún registro |

## No conformidades y acciones correctivas

**No conformidades** (`/no_conformidades/`, apartado 10.2 de ISO 9001:2026)
registra lo que ha fallado, cómo se contuvo, su causa raíz, las acciones
correctivas emprendidas y si fueron eficaces. Todos los roles registran no
conformidades y acciones correctivas.

### Pantallas

- **La lista** se filtra por **Descripción**, **Estado**, **Origen**,
  **Gravedad** y **Detectada el** (fecha de detección). Cada fila tiene
  **Ver**, **Editar** (mientras está abierta), **PDF** y, para los
  administradores, **Eliminar**.
- **+ Nueva no conformidad** abre el formulario; **Registrar no conformidad**
  la guarda.
- **La página de una no conformidad** (**No conformidad n.º** *N*) muestra su
  **Estado** con el botón de cierre, sus **Datos**, la tabla **Acciones
  correctivas** y, al final, el formulario de cancelación.

### Campos

- **Descripción** y **Fecha Detectada**, ambas obligatorias;
- **Origen**: **Auditoría**, **Cliente**, **Proceso**, **Proveedor** u
  **Otro**;
- **Proveedor (solo con el origen «Proveedor»)**: el proveedor externo que la
  causó (véase [Proveedores externos](#proveedores-externos));
- **Gravedad**: **Mayor**, **Menor** u **Observación**;
- **Responsable** (texto libre heredado) y **Responsable (persona)**;
- **Contención** y **Causa raíz**;
- **Acción Correctiva**: un campo de texto libre que se conserva de versiones
  anteriores. Las acciones correctivas estructuradas que se describen más
  abajo lo sustituyen; cuando tiene texto, la página lo muestra como **Acción
  correctiva (texto anterior)**.

### Estados

Una no conformidad pasa por **Abierta** → **Acción planificada** → **En
verificación** → **Cerrada**, o termina **Cancelada**. Nadie elige el estado
en el formulario:

- mientras está abierta, sigue a sus acciones correctivas:
  - sin acciones, está **Abierta**;
  - la primera acción la pasa a **Acción planificada**, donde permanece
    mientras haya alguna acción sin realizar;
  - cuando todas las acciones están realizadas, pasa a **En verificación**;
  - cuando la última acción se verifica como no eficaz, vuelve a **Acción
    planificada**: registre una nueva acción;
- el cierre, la cancelación y la reapertura son botones, que se describen más
  abajo.

### Acciones correctivas

En la página de la no conformidad, **+ Añadir acción** registra una acción:

- **Descripción**;
- **Responsable** (una persona);
- **Fecha prevista**;
- **Fecha de realización**, cuando se ha realizado.

El estado de la acción se deduce de sus datos: **Planificada**, **Realizada**,
**Verificada eficaz** o **Verificada no eficaz**.

- La fecha de realización no puede ser anterior a la fecha de detección, y la
  fecha de detección no puede llevarse más allá de la fecha de realización de
  una acción.
- Una acción verificada ya no se puede editar: **Una acción correctiva
  verificada no se puede modificar.** Un administrador todavía puede
  eliminarla mientras la no conformidad está abierta.

### Verificación de la eficacia

Los administradores y los auditores pulsan **Verificar** en una acción
realizada y registran:

- **Resultado**: **Eficaz** o **No eficaz**;
- **Fecha de verificación**: no anterior a la fecha de realización;
- **Verificada por**: nunca el responsable de la acción, que no aparece en el
  selector;
- **Evidencia**.

Pulse **Registrar verificación**. Solo se puede verificar una acción
realizada: **Solo se puede verificar una acción correctiva realizada; indica
antes su fecha de realización.**

### Cierre, cancelación y reapertura

- **Para cerrarla**, un administrador o un auditor pulsa **Cerrar no
  conformidad**. Requiere al menos una acción, todas las acciones realizadas y
  verificadas, y la última acción verificada como eficaz. Una acción anterior
  no eficaz se conserva como evidencia y no impide el cierre cuando una acción
  posterior ha resultado eficaz. Mientras falte algo, la página lo enumera
  bajo **No se puede cerrar todavía:** (por ejemplo, *1 acción pendiente de
  verificar* o *la última acción correctiva no fue eficaz; registra una nueva
  acción correctiva*).
- **Para cancelarla**, un administrador o un auditor escribe el **Motivo de la
  cancelación** al final de la página y pulsa **Cancelar no conformidad**. El
  motivo es obligatorio.
- Una no conformidad cerrada o cancelada, y sus acciones, son de solo
  lectura: **Esta no conformidad está cerrada o cancelada y no se puede
  modificar; un administrador puede reabrirla.**
- **Para reabrirla**, un administrador pulsa **Reabrir**. La no conformidad
  vuelve al estado que corresponde a sus acciones y pierde la fecha de cierre
  y el motivo de cancelación.
- **Eliminar** una no conformidad (administradores) elimina también sus
  acciones correctivas. El hallazgo de auditoría que la abrió sigue
  existiendo, sin el vínculo.

### PDF

**PDF** genera el **Informe de no conformidad**: sus campos, su estado y la
fecha de cierre o de cancelación (con el motivo), y una tabla de sus acciones
correctivas con sus fechas, su estado y su verificación (resultado, fecha,
verificador y evidencia).

## Control de documentos

**Documentos** (`/documents/`, apartado 7.5 de ISO 9001:2026) controla la
información documentada del SGC. Un documento conserva su identidad y su
propietario; su texto está en revisiones numeradas (1, 2, 3…) que se revisan
y se aprueban antes de entrar en vigor.

### Quién ve qué

- Todos los roles consultan la revisión en vigor (**Vigente**) de cada
  documento.
- Los borradores, las revisiones en revisión y las revisiones aprobadas aún
  no publicadas solo las ven los administradores y los auditores. Un usuario
  con rol Operativo no ve los documentos que no tienen ninguna revisión
  vigente.
- Los administradores y los auditores crean documentos, redactan borradores,
  los envían a revisión, los rechazan, los publican y adjuntan ficheros. Solo
  los administradores aprueban y dan de baja.

### Pantallas

- **La lista** se filtra por **Categoría**, **Propietario**, **Estado**
  (**Vigente**, **Sin publicar**, que nunca se ha publicado, o **De baja**) y
  **Solo con la revisión periódica vencida**. Muestra el **Código**, el
  **Título**, la **Categoría**, el **Propietario**, la **Revisión vigente**,
  la **Próxima revisión** con la etiqueta **Vencida** y el **Estado**.
- **La página de un documento** muestra sus **Datos**, la revisión vigente con
  su texto y su fichero, la revisión en preparación (para administradores y
  auditores) y el **Historial de revisiones**: número, estado, autor, quien la
  aprobó, fecha de aprobación, vigente desde, obsoleta desde, resumen de
  cambios, último rechazo y fichero.

### Creación de un documento

Pulse **+ Nuevo documento** y rellene:

- **Título** y **Código de Identificación** (único);
- **Categoría**: **Manual de Calidad**, **Procedimiento Operativo**,
  **Instrucción de Trabajo**, **Plan de Acción Correctiva**, **Plan de Acción
  Preventiva**, **Registro de Calidad**, **Informe de Revisión por la
  Dirección**, **Política de Seguridad y Salud Ocupacional**, **Indicador de
  Desempeño**, **Plan de Capacitación** u **Otro**;
- **Propietario** (una persona; obligatorio);
- **Próxima revisión** (fecha de la siguiente revisión periódica; opcional);
- **Autor de la revisión 1** y **Contenido** (el texto de la revisión 1).

El documento empieza con la revisión 1 en **Borrador**.

**Editar datos** cambia después los campos propios del documento. El código
solo se puede cambiar hasta que se publica una revisión; a partir de entonces
se muestra en modo de solo lectura y se rechaza cualquier cambio.

### Flujo de revisiones

| Estado | Significado | Paso siguiente |
|---|---|---|
| **Borrador** | Borrador en redacción | **Enviar a revisión** |
| **En revisión** | Pendiente de aprobación | **Aprobar** o **Rechazar** (vuelve a borrador) |
| **Aprobado** | Aprobado, aún no vigente | **Publicar** |
| **Vigente** | En vigor | La sustituye la siguiente revisión publicada |
| **Obsoleto** | Sustituido o dado de baja; se conserva como evidencia | Ninguno |

1. **Redactar.** **Editar borrador** cambia el **Contenido**, el **Resumen de
   cambios** y el **Autor de la revisión**.
2. **Enviar.** **Enviar a revisión** requiere el resumen de cambios: **El
   resumen de cambios es obligatorio para enviar la revisión a revisión.**
3. **Aprobar o rechazar.**
   - Un administrador pulsa **Aprobar** y elige en **Aprobada por** a la
     persona que aprueba. Quien aprueba nunca puede ser el autor de la
     revisión: el autor no puede ser ni la persona indicada ni la persona
     vinculada al usuario que aprueba (**El autor de una revisión no puede
     aprobarla.**).
   - **Rechazar** devuelve la revisión a borrador con un **Motivo del
     rechazo**, que el historial muestra como **Último rechazo**.
4. **Publicar.** **Publicar** pone en vigor una revisión aprobada desde hoy;
   la revisión a la que sustituye pasa a **Obsoleto** el mismo día. Las
   revisiones vigentes u obsoletas ya no cambian nunca.
5. **Siguiente revisión.** En un documento vigente sin nada en preparación,
   **+ Nueva revisión** inicia un borrador a partir del texto vigente; indique
   su **Autor de la revisión** y, si lo desea, un resumen de cambios. Un
   documento tiene como máximo una revisión en preparación (**El documento ya
   tiene una revisión en preparación.**) y como máximo una vigente.
6. **Descartar.** **Descartar borrador** elimina un borrador posterior y su
   fichero. La revisión 1 de un documento que nunca ha estado vigente no se
   puede descartar: dé de baja el documento.

### Ficheros adjuntos

Cada revisión puede llevar un fichero: PDF, DOCX, XLSX u ODT.

- **Para adjuntarlo** mientras la revisión es un borrador, elija el fichero
  bajo la revisión en preparación y pulse **Adjuntar**. Un fichero nuevo
  sustituye al anterior; **Quitar fichero** lo quita.
- La página muestra el tamaño máximo (20 MB, salvo que el servidor fije otro).
- El tipo de fichero se comprueba por su contenido además de por su
  extensión:
  - **Solo se admiten ficheros PDF, DOCX, XLSX u ODT.**;
  - **La extensión del fichero no corresponde a su contenido.**;
  - **El fichero está vacío.**
- Una vez vigente, la revisión y su fichero no cambian nunca.
- **Descarga:** todos los roles descargan el fichero de la revisión vigente;
  solo los administradores y los auditores descargan los ficheros de las demás
  revisiones. El fichero siempre se descarga; el navegador no lo abre.

Cómo almacena el servidor los ficheros y cómo se hacen sus copias de
seguridad se explica en la
[guía de operaciones](operaciones.md#ficheros-adjuntos-de-los-documentos).

### Baja

Los documentos nunca se eliminan. Un administrador pulsa **Dar de baja** y
escribe el **Motivo de la baja**:

- la revisión vigente pasa a obsoleta;
- se registran la fecha, el motivo y la persona del administrador;
- el documento pasa a ser de solo lectura (**El documento está dado de baja y
  no se puede modificar.**) y muestra **De baja**.

### Revisión periódica

Un documento en uso cuya fecha de **Próxima revisión** ha pasado aparece
marcado como **Vencida** en la lista y en su página. La tarjeta **Revisión de
documentos** del panel de control enumera las revisiones vencidas o que
vencen en los próximos 30 días, y un correo semanal se lo recuerda a los
propietarios y a los administradores (véase
[Notificaciones por correo electrónico](#notificaciones-por-correo-electrónico)).
Actualizar la fecha de la próxima revisión con **Editar datos** quita la
marca.

## Programa de auditoría, auditorías y hallazgos

Las auditorías internas (apartado 9.2 de ISO 9001:2026) se planifican en un
programa anual, las realiza un equipo independiente y se documentan con
hallazgos clasificados. Los hallazgos de no conformidad alimentan el ciclo de
acciones correctivas. Los administradores y los auditores usan estas
pantallas; los usuarios con rol Operativo no tienen acceso.

### Programa de auditoría

**Programas de auditoría** (`/programas-auditoria/`, apartado 9.2.2) contiene
un programa por año.

- **Para crearlo**, pulse **+ Nuevo programa** y rellene **Año** (de 2000 a
  2100, un programa por año), **Objetivos** y **Alcance**. Empieza en
  **Borrador**.
- **Para aprobarlo**, un administrador pulsa **Aprobar** en su página y elige
  **Aprobado por**. El selector empieza con la persona del propio
  administrador, y la fecha de aprobación es la de hoy. El programa pasa a
  **Aprobado**.
- El año de un programa aprobado no cambia nunca, ni tampoco el de un
  borrador que ya tiene auditorías.
- **La página del programa** muestra su estado y su aprobación, sus objetivos
  y su alcance, y **Auditorías del programa**: cada auditoría con su fecha,
  su área, su auditor jefe, su estado y sus hallazgos contados por tipo
  (hasta las primeras 100 auditorías). **+ Nueva auditoría** en esa página
  crea una auditoría ya incluida en el programa.
- No se puede eliminar un programa que tiene auditorías: **No se puede
  eliminar un programa que tiene auditorías; quítalas antes.**
- **PDF** genera el programa: año, estado, quien lo aprobó y fecha de
  aprobación, objetivos y alcance, y todas las auditorías por fecha con su
  área, su auditor jefe, su estado y sus hallazgos por tipo.

### Auditorías

**Auditorías** (`/auditorias/`) muestra las auditorías de diez en diez y se
filtra por **Área auditada**, **Auditor**, **Estado**, **Desde** y **Hasta**
(intervalo de fechas). Haga clic en el área de una auditoría para abrir su
página.

**Para planificar una auditoría**, pulse **+ Nueva auditoría** y rellene:

- **Área Auditada** y **Fecha**;
- **Programa de auditoría** (opcional): la fecha de la auditoría debe caer en
  el año del programa;
- **Auditor** (texto heredado) y **Auditor (persona)**: el auditor jefe;
- **Equipo auditor** (varias personas);
- **Responsable del área auditada** (una persona);
- **Alcance** y **Criterios de auditoría** (por ejemplo, *ISO 9001:2026,
  procedimientos internos*);
- **Resultado** (obligatorio) y **Acción Correctiva** (texto libre);
- **Estado**: **Pendiente**, **En Proceso**, **Completada** o **Cancelada**.

**Independencia.** Ni el auditor jefe ni ningún miembro del equipo pueden ser
el responsable del área auditada. El formulario vuelve con el motivo: **El
auditor jefe no puede ser el responsable del área auditada.** o **Ningún
miembro del equipo auditor puede ser el responsable del área auditada.**

**La página de la auditoría** (**Auditoría n.º** *N*) muestra la
planificación, el alcance y los criterios, el resultado y los **Hallazgos**.

**Eliminar** una auditoría (administradores) se rechaza mientras tenga
hallazgos: **No se puede eliminar una auditoría que tiene hallazgos; elimina
antes sus hallazgos.**

**PDF** genera el **Informe de auditoría**: programa, área, responsable del
área, auditor jefe, equipo, fecha, estado, alcance, criterios, resultado y
acción correctiva, y una tabla de hallazgos (tipo, cláusula, descripción,
evidencia y número de no conformidad).

### Hallazgos

En la página de la auditoría, **+ Añadir hallazgo** registra un hallazgo:

- **Tipo**: **No conformidad mayor**, **No conformidad menor**,
  **Observación** u **Oportunidad de mejora**;
- **Cláusula de la norma** (opcional): números separados por puntos del 4 al
  10, como `8.5.1`;
- **Descripción** (obligatoria) y **Evidencia**.

**Reglas:**

- Un hallazgo nunca pasa a otra auditoría.
- Una auditoría completada sigue admitiendo hallazgos. Los hallazgos de una
  auditoría cancelada son de solo lectura: **No se pueden añadir ni modificar
  hallazgos de una auditoría cancelada.**

### No conformidades abiertas por hallazgos

Un hallazgo **No conformidad mayor** o **No conformidad menor** abre su no
conformidad al guardarse, y la confirmación la indica (**Se ha abierto la no
conformidad n.º** *N*). La no conformidad toma:

- el origen **Auditoría** y la gravedad **Mayor** o **Menor**;
- la fecha de la auditoría como fecha de detección;
- la descripción *Hallazgo de auditoría (área, fecha): descripción del
  hallazgo*;
- el responsable del área auditada como responsable, si esa persona está
  activa.

El hallazgo queda vinculado a la no conformidad, que sigue después el
[ciclo de acciones correctivas](#no-conformidades-y-acciones-correctivas).
A partir de entonces:

- cambiar el hallazgo entre *mayor* y *menor* cambia la gravedad de la no
  conformidad solo mientras esta está **Abierta**; en otro caso, el cambio se
  rechaza;
- cambiar el hallazgo a otro tipo conserva la no conformidad y el vínculo;
  volver al tipo anterior reutiliza ese vínculo, o abre una nueva no
  conformidad si la anterior se eliminó;
- las demás modificaciones nunca afectan a la no conformidad, que es un
  registro propio;
- eliminar un hallazgo (administradores) nunca elimina su no conformidad, y
  eliminar la no conformidad borra el vínculo del hallazgo.

## Contexto de la organización

**Contexto** (`/contexto/`), en el menú **Contexto**, es el registro de las
cuestiones externas e internas del apartado 4.1 de ISO 9001:2026: los factores
que pueden afectar a la capacidad de la organización para lograr los
resultados previstos de su SGC. Recoge también lo que la norma pide decidir
sobre el cambio climático: si es una cuestión pertinente. Todos los roles
consultan el registro; los administradores y los auditores crean y editan
cuestiones; solo los administradores las eliminan.

### La cuestión

Pulse **+ Nueva cuestión** y rellene:

- **Tipo**: **Externa** o **Interna**;
- **Categoría (según el tipo)**: en una cuestión externa, **Política**,
  **Económica**, **Social**, **Tecnológica**, **Legal** o **Ambiental**; en
  una interna, **Cultura**, **Recursos**, **Conocimiento**, **Desempeño** o
  **Estructura**. El selector ofrece los dos grupos, pero el sistema rechaza
  una categoría del otro tipo: **La categoría no corresponde al tipo: una
  cuestión externa es política, económica, social, tecnológica, legal o
  ambiental, y una interna, de cultura, recursos, conocimiento, desempeño o
  estructura.**;
- **Título** y **Descripción**;
- **Efecto** sobre los resultados del SGC: **Positivo**, **Negativo** o
  **Neutro**;
- **Pertinente para el SGC** (**Sí** o **No**) y **Justificación de la
  pertinencia (obligatoria si no es pertinente)**;
- **Relacionada con el cambio climático**;
- **Responsable** (una persona, opcional);
- **Fecha de identificación**: nunca futura (**La fecha de identificación no
  puede ser futura.**);
- **Próxima revisión** (opcional): nunca anterior a la fecha de
  identificación;
- **Estado**: **Vigente** o **Superada**;
- **Notas**.

### Pertinencia y cambio climático

- Una cuestión no pertinente explica por qué: **Indica por qué la cuestión no
  es pertinente («justificacion_pertinencia»).**
- Marque **Relacionada con el cambio climático** en las cuestiones que tienen
  que ver con él, sean pertinentes o no. El registro deja así constancia de
  que la organización ha determinado si el cambio climático es una cuestión
  pertinente: una cuestión marcada y pertinente lo afirma; una marcada y no
  pertinente lo descarta, con su justificación.
- Una cuestión que deja de aplicar pasa a **Superada**: no se elimina y queda
  como evidencia.
- **Regla de vencimiento.** Una cuestión vigente cuya **Próxima revisión** es
  anterior a hoy muestra **Revisión vencida**, y el panel de control la
  señala.

### Pantallas

- **La lista**, por tipo, categoría y título, se filtra por **Tipo**,
  **Categoría**, **Pertinente**, **Cambio climático**, **Estado**,
  **Responsable** y **Revisión vencida**. Muestra de cada cuestión su efecto,
  su pertinencia, si se relaciona con el cambio climático (**Clima**), su
  responsable, su próxima revisión (con una etiqueta si está vencida), su
  estado y cuántos riesgos y oportunidades nacen de ella.
- **La página de una cuestión** muestra todos sus campos, enlaza sus riesgos y
  sus oportunidades, y abre las listas de riesgos y de oportunidades filtradas
  por ella.
- **Eliminación**: solo se puede eliminar una cuestión a la que no cita ningún
  riesgo ni oportunidad. En otro caso, su página no muestra **Eliminar** y el
  sistema la rechaza: **No se puede eliminar una cuestión del contexto a la
  que se refieren riesgos u oportunidades; quítala antes de esos registros.**

### Riesgos y oportunidades de una cuestión

Un riesgo o una oportunidad puede indicar la **Cuestión del contexto** de la
que nace (véase [Riesgos y oportunidades](#riesgos-y-oportunidades)). El
selector solo ofrece las cuestiones vigentes, y el sistema rechaza una
superada: **La cuestión del contexto está superada; elige una vigente.** Un
registro que ya citaba una cuestión que después se supera la conserva al
editarse: el selector la muestra con **(superada)**.

## Partes interesadas

**Partes interesadas** (`/partes_interesadas/`), en el menú **Contexto**, es el
registro del apartado 4.2: las partes interesadas pertinentes para el SGC y
sus necesidades y expectativas. Todos los roles consultan, crean y editan
partes interesadas; solo los administradores las eliminan.

Pulse **+ Nueva parte interesada** y rellene:

- **Nombre** (obligatorio y único: **Ya existe una parte interesada con ese
  nombre.**);
- **Tipo**: **Externa** o **Interna**, o **— Sin indicar —**;
- **Pertinente para el SGC**: **Sí**, **No** o **Sin evaluar**;
- **Responsable** (una persona, opcional);
- **Próxima revisión** (opcional): al indicarla o cambiarla, nunca es anterior
  a hoy (**La fecha de la próxima revisión («fecha_revision») no puede ser
  anterior a hoy.**);
- **Tiene requisitos relacionados con el cambio climático**;
- **Necesidades y Expectativas**, **Requisitos Identificados** y **Objetivo
  Estratégico**.

Las partes interesadas registradas antes de estos campos aparecen **Sin
evaluar** y sin tipo hasta que se editan.

- **La lista**, por nombre, muestra el tipo, la pertinencia, los requisitos
  climáticos (**Clima**), el responsable, la próxima revisión (con **Revisión
  vencida** si ya ha pasado, sea o no pertinente la parte) y las necesidades y
  expectativas. El panel de control enlaza la lista filtrada por las que
  tienen la revisión vencida.
- **La página de una parte interesada** muestra su identificación y sus
  textos.
- Un riesgo o una oportunidad puede citar su **Parte interesada**.

## Alcance del SGC

**Alcance del SGC** (`/alcance/`), en el menú **Contexto**, recoge el alcance
del apartado 4.3: la declaración del alcance, los productos y servicios, los
centros y, para cada requisito de los capítulos 4 a 10, si aplica y, si no
aplica, por qué. El alcance se guarda por versiones numeradas. Todos los roles
consultan todas las versiones y sus PDF; los administradores y los auditores
preparan los borradores; solo los administradores los aprueban.

### Versiones y aprobación

| Estado | Significado |
|---|---|
| **Borrador** | La versión en preparación, la única que cambia; solo hay un borrador a la vez |
| **Vigente** | La versión aprobada en vigor; solo hay una |
| **Obsoleto** | Una versión sustituida por otra; se conserva como evidencia |

- **+ Nuevo borrador** empieza la versión siguiente con los textos y la
  aplicabilidad de la versión vigente, que se cambian después. En el primer
  borrador aplican todos los requisitos y la declaración es obligatoria: **La
  primera versión del alcance necesita su declaración («declaracion»).**
- Mientras hay un borrador, la página del alcance lo enlaza y no ofrece otro:
  **Ya hay un borrador del alcance; modifícalo o elimínalo antes de empezar
  otro.**
- **Editar** cambia los textos del borrador (**Declaración del alcance**,
  **Productos y servicios**, **Centros** y **Notas**) y su aplicabilidad.
  **Eliminar** borra el borrador, y su número de versión queda libre.
- **Aprobar**, solo para administradores, pone el borrador en vigor con la
  fecha de hoy y deja obsoleta desde hoy la versión vigente. Queda registrada
  como aprobadora la persona vinculada a su cuenta de usuario (**Tu usuario no
  está vinculado a ninguna persona; vincúlalo para aprobar el alcance.**);
  puede aprobar un borrador que haya preparado usted.
- Las versiones vigentes y obsoletas nunca cambian: **Una versión vigente u
  obsoleta del alcance no se puede modificar.**

### Aplicabilidad

El formulario del borrador tiene una fila por cada uno de los 38 requisitos
del catálogo, de 4.1 a 10.3, con la casilla **Aplica** y una justificación.
Desmarque **Aplica** en los requisitos que no aplican y justifique cada uno;
sin justificación, el error aparece en su fila y no se guarda nada: por
ejemplo, **Justifica por qué no aplica el apartado 8.3 (Diseño y desarrollo de
los productos y servicios).**

### Pantallas del alcance

- **Alcance del SGC** muestra la versión vigente: su número, su estado, su
  aprobación, la declaración, los productos y servicios, los centros, las
  notas y la tabla **Aplicabilidad de los requisitos**. Sin versión vigente
  muestra **Todavía no hay un alcance aprobado.** y enlaza el borrador, si lo
  hay.
- **Historial** lista todas las versiones, de la más reciente a la más
  antigua, con su estado, quién la aprobó y cuándo, y desde cuándo está
  obsoleta.
- **La página de una versión** muestra lo mismo que la vigente y, en un
  borrador, **Editar**, **Eliminar** y **Aprobar** según el rol.

### PDF del alcance

**PDF**, en la página del alcance vigente y en la de cada versión, genera esa
versión del alcance (`alcance_sgc_v<N>.pdf`):

- la versión, su estado, quién la aprobó y cuándo, desde cuándo está
  obsoleta, y cuántos requisitos aplican y cuántos no;
- la declaración del alcance, los productos y servicios, los centros y las
  notas;
- la tabla de aplicabilidad: apartado, requisito, **Aplica** o **No aplica**
  y justificación;
- al final, los requisitos que no aplican con su justificación, o **Todos los
  requisitos aplican.**

Cualquier versión se exporta, también un borrador, que el PDF identifica por
su estado. Sin versión vigente, el PDF del alcance vigente no se genera:
**No hay ninguna versión vigente del alcance.**

## Procesos

**Procesos** (`/procesos/`), en el menú **Calidad**, es el mapa de procesos del
SGC (apartados 4.4 y 8.1 de ISO 9001:2026): los procesos, su responsable, lo
que reciben y lo que entregan, los recursos y los criterios con que funcionan
y los procesos con los que interactúan. Los objetivos de la calidad y los
riesgos y oportunidades eligen su proceso en este registro. Todos los roles
consultan los procesos; los administradores y los auditores los crean y los
editan; solo los administradores los eliminan.

### El proceso

Para añadir un proceso, pulse **+ Nuevo proceso** y rellene:

- **Nombre** (obligatorio y único: **Ya existe un proceso con ese nombre.**);
- **Tipo**: **Estratégico**, **Operativo** o **Soporte** (opcional);
- **Responsable**: la persona responsable del proceso (opcional);
- **Procesos relacionados**: los procesos con los que interactúa (para elegir
  varios, mantenga pulsada la tecla Ctrl, o ⌘ en Mac);
- **Entradas**, **Salidas** y **Recursos**;
- **Criterios de operación** (8.1) y **Criterios de aceptación** de sus
  salidas;
- **Requiere control de proveedores externos**;
- **Notas**.

**Reglas:**

- La relación vale en ambos sentidos: la página de cada proceso relacionado
  también muestra este. Un proceso no puede relacionarse consigo mismo, y el
  selector no lo ofrece.
- Al editar, los procesos relacionados elegidos sustituyen a los anteriores;
  si no elige ninguno, el proceso deja de estar relacionado con otros.

### Pantallas

- **La lista**, por nombre, muestra el tipo, el responsable y cuántos procesos
  relacionados, objetivos de la calidad y riesgos y oportunidades tiene cada
  proceso. **Filtros**: **Tipo** y **Responsable** (solo se ofrecen las
  personas responsables de algún proceso).
- **La página de un proceso** muestra todos sus campos, sus **Procesos
  relacionados** y los **Registros que citan el proceso**: **Objetivos de
  calidad**, **Riesgos** y **Oportunidades**, cada uno con su enlace.
- **Eliminación**: solo se puede eliminar un proceso que no tiene procesos
  relacionados y al que no cita ningún objetivo, riesgo ni oportunidad. En otro
  caso, su página no muestra **Eliminar** y el sistema rechaza la eliminación
  con uno de estos mensajes:
  - **No se puede eliminar un proceso relacionado con otros procesos; quita
    antes esas relaciones.**
  - **No se puede eliminar un proceso al que se refieren objetivos de calidad;
    quítalo antes de esos objetivos.**
  - **No se puede eliminar un proceso al que se refieren riesgos u
    oportunidades; quítalo antes de esos registros.**
  - **No se puede eliminar un proceso vinculado a proveedores externos;
    quítalo antes de esos proveedores.**

## Riesgos y oportunidades

**Riesgos** (`/riesgos/`) y **Oportunidades** (`/oportunidades/`), en el menú
**Calidad**, son los dos registros del apartado 6.1 de ISO 9001:2026, que trata
los riesgos y las oportunidades como conceptos separados. Un riesgo se valora
por su probabilidad y su impacto; una oportunidad, por su beneficio y su
viabilidad. Cada registro tiene sus acciones, con responsable, fecha límite y
evaluación de la eficacia, y una valoración residual en el caso de los
riesgos. Todos los roles consultan los dos registros y sus PDF. Los
administradores y los auditores crean y editan registros, y añaden, editan y
evalúan acciones; solo los administradores eliminan.

### El registro

Pulse **+ Nuevo riesgo** o **+ Nueva oportunidad** y rellene:

- **Título** y **Descripción**;
- **Origen**: **Interno** o **Externo** a la organización;
- **Parte interesada** (opcional; véase
  [Partes interesadas](#partes-interesadas)), **Cuestión del contexto**
  (opcional; solo las vigentes, véase
  [Contexto de la organización](#contexto-de-la-organización)) y **Proceso**
  (opcional; véase [Procesos](#procesos));
- **Responsable** (una persona);
- la valoración, el **Tratamiento** del riesgo o la **Decisión** sobre la
  oportunidad (véase [Valoración](#valoración)) y el **Estado** (véase
  [Estados y revisión](#estados-y-revisión));
- **Fecha de identificación**: el formulario empieza con la de hoy; nunca es
  futura;
- **Próxima revisión** (opcional): nunca anterior a la fecha de
  identificación;
- **Notas**.

Un registro con acciones no se puede eliminar: **No se puede eliminar un
riesgo que tiene acciones; elimina antes sus acciones.** (o el mismo mensaje
para una oportunidad).

### Valoración

Cada valor va de 1 a 5. El producto de los dos valores de una pareja da el
nivel del riesgo o la prioridad de la oportunidad:

| Registro | Valores | Resultado |
|---|---|---|
| Riesgo | **Probabilidad (1-5)** × **Impacto (1-5)** | **Nivel**: **Bajo** 1–4, **Medio** 5–9, **Alto** 10–16, **Crítico** 20–25 |
| Oportunidad | **Beneficio (1-5)** × **Viabilidad (1-5)** | **Prioridad**: **Baja** 1–6, **Media** 8–12, **Alta** 15–25 |

- Los dos valores de una pareja se indican a la vez o se dejan los dos en
  **Sin valorar**. Mientras tanto, el registro aparece como **Sin valorar**:
  una tarea pendiente que señala el panel de control.
- **Tratamiento** de un riesgo: **Aceptar**, **Mitigar**, **Transferir** o
  **Evitar**. **Decisión** sobre una oportunidad: **Aprovechar**, **Posponer**
  o **Descartar**. Los dos pueden quedar en **— Sin decidir —**.
- **Valoración residual** de un riesgo: **Probabilidad residual (tras las
  acciones)** e **Impacto residual (tras las acciones)** dan el **Nivel
  residual** con las mismas bandas. Necesita antes la valoración del riesgo:
  **La valoración residual necesita antes la valoración del riesgo
  («probabilidad» e «impacto»).**

### Estados y revisión

| Registro | Estados abiertos | Estados cerrados |
|---|---|---|
| Riesgo | **Identificado**, **En tratamiento**, **Controlado** | **Cerrado** |
| Oportunidad | **Identificada**, **En curso** | **Aprovechada**, **Descartada** |

- El estado se cambia en el formulario del registro.
- Un registro cerrado no admite acciones nuevas: **El riesgo está cerrado: no
  admite acciones nuevas.** o **La oportunidad está aprovechada o descartada:
  no admite acciones nuevas.** Sus acciones existentes se siguen editando y
  evaluando.
- **Regla de vencimiento.** Un registro abierto cuya **Próxima revisión** es
  anterior a hoy muestra **Revisión vencida**. Un registro cerrado nunca está
  vencido.

### Acciones

En la página del registro, **+ Nueva acción** registra:

- **Descripción**;
- **Responsable** (una persona);
- **Fecha límite**;
- **Estado**: **Pendiente** (al empezar), **En curso**, **Completada** o
  **Cancelada**;
- **Fecha de finalización (solo si está completada)**: obligatoria en una
  acción completada, nunca futura y solo en ese estado.

Una acción pendiente o en curso cuya fecha límite ya ha pasado muestra
**Vencida**. Una acción nunca pasa a otro registro.

**Evaluación de la eficacia (6.1.2 b).** Una acción completada muestra
**Evaluar eficacia** (o **Reevaluar** si ya se evaluó), que registra:

- **Resultado**: **Eficaz** o **No eficaz**;
- **Fecha de la evaluación**: el formulario empieza con la de hoy; nunca es
  anterior a la finalización de la acción ni futura;
- **Comentario**.

Solo se evalúa una acción completada: **Solo se evalúa la eficacia de una
acción completada.**

### Pantallas

- **La lista** se filtra por **Estado**, **Nivel** (o **Prioridad**),
  **Responsable**, **Proceso**, **Cuestión del contexto**, **Origen**, **Sin
  valorar**, **Revisión vencida** y **Abierto** (o **Abierta**); los riesgos, además, por
  **Probabilidad**, **Impacto** y **Alto o crítico sin acción abierta**.
  Muestra de cada registro su proceso, su responsable, su nivel y su nivel
  residual con el tratamiento (o su prioridad con la decisión), su estado, su
  **Próxima revisión** (con una etiqueta si está vencida) y sus **Acciones
  abiertas** (pendientes o en curso).
- **El mapa de calor**, sobre la lista de riesgos, es una cuadrícula de 5 × 5:
  la probabilidad en las filas (5 arriba) y el impacto en las columnas. Cada
  casilla cuenta los riesgos abiertos valorados con esa probabilidad y ese
  impacto, tiene el color de su nivel y abre la lista filtrada por ella.
- **La página de un registro** muestra su **Valoración** (con el nivel antes y
  después de las acciones en un riesgo), su **Identificación**, su
  **Descripción**, sus **Notas** y la tabla **Acciones**.

### PDF

**PDF**, en cada lista, genera el registro completo a fecha de hoy:

- un resumen de los registros abiertos: cuántos hay por nivel (o prioridad),
  cuántos están **Sin valorar** y cuántos tienen la **Revisión vencida**, y
  cuántos están cerrados;
- en el de riesgos, el mapa de calor como tabla;
- los registros abiertos y, en otra sección, los cerrados, cada grupo por
  nivel (o prioridad) de mayor a menor, con los no valorados al final, y por
  título. De cada registro: proceso, responsable, origen, nivel y nivel
  residual con el tratamiento (o prioridad y decisión), estado, próxima
  revisión y descripción;
- una tabla de sus acciones: descripción, responsable, fecha límite, estado y
  eficacia (resultado, fecha y comentario).

Los riesgos por nivel y las oportunidades por prioridad son también entradas
de la revisión por la dirección (véase [Entradas (9.3.2)](#entradas-932)).

## Objetivos de la calidad

**Objetivos de calidad** (`/objetivos-calidad/`, apartado 6.2 de ISO
9001:2026) registra lo que el SGC se propone lograr, cómo se mide y si va por
buen camino. Todos los roles consultan los objetivos y las mediciones, porque
los objetivos de la calidad deben comunicarse. Los administradores y los
auditores crean, editan, registran mediciones y cambian el estado de un
objetivo; solo los administradores eliminan.

### El objetivo

Pulse **+ Nuevo objetivo** y rellene:

- **Título** y **Descripción**;
- **Proceso** (opcional; los procesos proceden del registro de procesos, véase
  [Otros registros](#otros-registros));
- **Responsable** (una persona);
- **Indicador** y **Unidad** (por ejemplo, %, días);
- **Meta** y **Sentido de la meta**: con **≥ igual o superior a la meta** se
  cumple al alcanzar o superar la meta; con **≤ igual o inferior a la meta**,
  al quedar en la meta o por debajo;
- **Valor inicial** (opcional);
- **Frecuencia de medición**: **Mensual**, **Trimestral**, **Semestral** o
  **Anual**;
- **Fecha de inicio** y **Fecha límite** (no anterior al inicio);
- **Año** (si se deja vacío, toma el año de la fecha de inicio);
- **Recursos necesarios** y **Cómo se evalúan los resultados**.

Los números admiten punto o coma decimal.

### Estados

Un objetivo empieza **Activo**. Su página lo cierra con **Alcanzado**, **No
alcanzado** o **Cancelar** (estado **Cancelado**); **Reactivar** lo vuelve a
activar. Solo un objetivo activo admite mediciones.

### Mediciones

En la página del objetivo, **+ Nueva medición** registra:

- **Fecha**: el formulario empieza con la de hoy;
- **Valor**;
- **Registrada por**: el formulario empieza con su propia persona;
- **Comentario**.

**Reglas:**

- Una medición por objetivo y fecha: **El objetivo ya tiene una medición en
  esa fecha.**
- La fecha está dentro del periodo del objetivo y nunca es futura.
- Las mediciones solo se añaden, editan y eliminan mientras el objetivo está
  **Activo**: **Solo se pueden añadir o modificar mediciones de un objetivo
  activo.**
- Cuando un objetivo tiene mediciones, su meta y su sentido quedan fijados y
  no se puede eliminar (elimine antes sus mediciones, mientras esté activo).
  Su periodo debe seguir incluyéndolas.

### Cumplimiento y tendencia

- Cada medición cumple la meta o no, y lo mismo el objetivo, según su última
  medición: **Cumple**, **No cumple** o, sin mediciones, **Sin medir**.
- **Tendencia** compara la última medición con la anterior: **Sube**, **Baja**
  o **Estable**.
- **Regla de vencimiento.** La siguiente medición de un objetivo activo vence
  un periodo de su frecuencia después de su última medición, o de su fecha de
  inicio si no tiene ninguna, y nunca después de su fecha límite. Una vez
  pasada esa fecha, el objetivo muestra **Medición vencida**.

### Pantallas

- **La lista** se filtra por **Año**, **Estado**, **Responsable** y
  **Proceso**. Muestra de cada objetivo el indicador, la meta, el último
  valor, **Meta cumplida**, el responsable, la fecha límite, la **Próxima
  medición** (con una etiqueta si está vencida) y el estado.
- **La página de un objetivo** muestra su **Resultado** (meta, valor inicial,
  último valor y si cumple la meta, tendencia, próxima medición), su
  **Planificación**, la tabla **Mediciones** y el gráfico **Evolución** de las
  mediciones frente a la meta.

### PDF

**PDF** *año*, en la lista, genera los objetivos del año del filtro **Año**,
o del año en curso:

- los objetivos contados por estado, cuántos objetivos activos cumplen su
  meta y cuántos tienen una medición vencida;
- cada objetivo, por fecha límite y título, con su proceso, indicador, meta,
  valor inicial, responsable, fecha límite, frecuencia, estado, último valor y
  su fecha, si cumple o no, tendencia, próxima medición, recursos y
  evaluación;
- una tabla de sus mediciones: fecha, valor, si cumple o no y comentario.

## Cambios en el SGC

**Cambios** (`/cambios/`, en el menú **Calidad**) registra los cambios en el
sistema de gestión de la calidad que se planifican según el apartado 6.3 de
ISO 9001:2026. Cada cambio guarda de dónde nace, su propósito y sus
consecuencias potenciales, cómo se mantiene la integridad del SGC, los
recursos, las responsabilidades y autoridades, a quién y cómo se comunica, los
procesos y documentos que afecta y las tareas que lo llevan a cabo. Después
registra su aprobación, su implantación y la revisión de su eficacia. Todos los
roles consultan los cambios, sus tareas y sus PDF. Los administradores y los
auditores proponen y editan cambios y tareas, registran la implantación y
revisan la eficacia; solo los administradores aprueban, cancelan y eliminan.

### El cambio

Pulse **+ Nuevo cambio** y rellene:

- **Título** y **Descripción**;
- **Origen**: **Revisión por la dirección**, **No conformidad**,
  **Auditoría**, **Riesgo**, **Oportunidad**, **Cuestión del contexto** u
  **Otro**;
- **Registro que lo origina (del origen elegido)** (opcional): el registro del
  que nace el cambio, con los registros agrupados por origen. Solo se ofrecen
  las acciones de revisión del tipo **Cambio en el SGC** y las cuestiones del
  contexto vigentes. Un registro de otro origen se rechaza (**El registro
  elegido no corresponde al origen «…»; elige uno de ese origen o
  ninguno.**). El origen **Otro** no tiene registro;
- las consideraciones del apartado 6.3: **a) Propósito del cambio**
  (obligatorio), **a) Consecuencias potenciales**, **b) Integridad del SGC**,
  **c) Disponibilidad de recursos** y **d) Asignación de responsabilidades y
  autoridades**. Las cuatro últimas pueden quedar en blanco al proponer el
  cambio, pero aprobarlo las exige;
- **Comunicación (a quién y cómo)**;
- **Responsable** (una persona);
- **Fecha de propuesta**: el formulario empieza con la de hoy; nunca es
  futura;
- **Fecha prevista de implantación** (opcional al proponerlo; aprobarlo la
  exige): nunca anterior a la fecha de propuesta;
- **Procesos afectados** y **Documentos afectados** (solo los documentos que
  el rol puede consultar).

Un registro que da origen a un cambio no se puede eliminar; por ejemplo:
**No se puede eliminar un riesgo al que se refieren cambios en el SGC.**

### Tareas del cambio

En la página del cambio, **+ Nueva tarea** registra:

- **Descripción**;
- **Responsable** (una persona);
- **Fecha límite**;
- **Estado**: **Pendiente** (al empezar), **En curso**, **Completada** o
  **Cancelada**;
- **Fecha de finalización (solo si está completada)**: obligatoria en una
  tarea completada, nunca futura y solo en ese estado;
- **Resultado**.

Las tareas se añaden, editan y eliminan mientras el cambio está propuesto o
aprobado; eliminarlas es cosa de los administradores. Una tarea nunca pasa a
otro cambio. Una tarea pendiente o en curso cuya fecha límite ya ha pasado,
en un cambio propuesto o aprobado, muestra **Vencida**.

### Ciclo de vida del cambio

| Estado | Qué se puede cambiar | Siguiente paso |
|---|---|---|
| **Propuesto** | Todo, con **Editar**, y sus tareas | Un administrador lo aprueba (**Aprobar**) o lo cancela |
| **Aprobado** | El responsable, la fecha prevista, la comunicación, los procesos, los documentos y las tareas | Un administrador o un auditor registra la implantación (**Registrar la implantación**); un administrador puede cancelarlo |
| **Implantado** | Nada | Un administrador o un auditor revisa la eficacia (**Revisar la eficacia**) |
| **Eficacia revisada** | Nada | Estado final |
| **Cancelado** | Nada | Estado final |

Las decisiones se toman en **Decidir sobre el cambio**, en la página del
cambio, que solo ofrece a cada rol las que puede tomar:

- **Aprobar** exige las cuatro consideraciones, la fecha prevista y al menos
  una tarea que no esté cancelada. Si falta algo, el mensaje lo enumera
  (**Para aprobar el cambio falta: …**). El cambio se aprueba hoy, a nombre
  de la persona vinculada al usuario. Desde entonces su plan (el título, la
  descripción, el origen con su registro, el propósito y las cuatro
  consideraciones) ya no cambia. La fecha prevista se puede mover, pero no
  quitar.
- **Registrar la implantación** exige que todas las tareas estén completadas
  o canceladas, y al menos una completada (**Quedan tareas pendientes o en
  curso; complétalas o cancélalas antes de implantar el cambio.**). La
  **Fecha de implantación** nunca es futura ni anterior a la aprobación.
- **Revisar la eficacia** registra el **Resultado** (**Eficaz** o **No
  eficaz**), la **Fecha de la revisión** (nunca anterior a la implantación ni
  futura) y el **Comentario (obligatorio si no ha sido eficaz)**. La revisa una persona distinta de la responsable del cambio (**La persona que
  revisa la eficacia no puede ser la responsable del cambio.**).
- **Cancelar el cambio** exige el **Motivo de la cancelación**; el cambio se
  cancela hoy y sus tareas quedan como estaban.
- **Eliminar** (administradores) solo es posible con un cambio propuesto, y
  elimina también sus tareas.

Un cambio aprobado está **Fuera de plazo** cuando su fecha prevista de
implantación es anterior a hoy. Ninguna de estas decisiones se toma desde el
servidor MCP (véase [Agentes y tokens de API](#agentes-y-tokens-de-api)).

### Pantallas de los cambios

- **La lista** se filtra por **Texto (título o descripción)**, **Estado**,
  **Origen**, **Responsable**, **Proceso**, **Fuera de plazo** y **Con tareas
  vencidas**, y se pagina; una página posterior a la última muestra la
  última. De cada cambio muestra el origen con el registro que lo origina, el
  responsable, el estado, la **Implantación prevista** (con la etiqueta
  **Fuera de plazo**) y las **Tareas abiertas** (pendientes o en curso).
- **La página del cambio** muestra el **Plan del cambio**, las
  **Consideraciones** de a) a d), los **Procesos afectados**, los
  **Documentos afectados** que el rol puede consultar (un usuario con rol
  Operativo solo ve los vigentes), las **Tareas** y **Aprobación,
  implantación y eficacia**. El registro que origina el cambio se enlaza
  para los roles que pueden consultarlo; los demás ven **Registro
  vinculado**.
- **Las páginas de los registros de origen.** La página de una no
  conformidad, una auditoría, un riesgo, una oportunidad o una cuestión del
  contexto muestra la tarjeta **Cambios en el SGC que origina**, con el
  estado de cada cambio. En la página de una revisión por la dirección, cada
  acción enumera los cambios que origina, y las acciones del tipo **Cambio en
  el SGC** ofrecen **Planificar cambio** a quien puede crear cambios: abre el
  formulario del cambio con el origen y la acción ya elegidos.
- **El panel de control** muestra la tarjeta **Cambios en el SGC** (véase
  [Panel de control](#panel-de-control)).

### PDF de los cambios

- **PDF**, en la lista, genera el registro de cambios a fecha de hoy:
  - cuántos cambios hay en cada estado y cuántos aprobados están fuera de
    plazo, con su lista;
  - los cambios agrupados por estado, primero los abiertos (propuestos,
    aprobados e implantados) y después los de eficacia revisada y los
    cancelados. De cada cambio: el origen con su registro, el responsable,
    las fechas de propuesta, prevista, de aprobación y de implantación, las
    tareas completadas sobre el total y el resultado (el de la eficacia, o la
    fecha y el motivo de la cancelación).
- **Plan PDF**, en la página del cambio, genera su plan:
  - el título, el estado, el origen con su registro, el responsable y las
    fechas de propuesta y prevista;
  - la descripción, las consideraciones de a) a d) y la comunicación;
  - los procesos y los documentos afectados, con la misma regla de
    visibilidad que la página;
  - las tareas: descripción, responsable, fecha límite (y si está vencida),
    estado, fecha de finalización y resultado;
  - la aprobación, la implantación, la revisión de la eficacia y, si se
    canceló, la cancelación.

Los cambios del periodo son también una entrada de la revisión por la
dirección (véase [Entradas (9.3.2)](#entradas-932)).

## Proveedores externos

**Proveedores** (`/proveedores/`), en el menú **Calidad**, es el registro de
los proveedores externos de procesos, productos y servicios (apartado 8.4 de
ISO 9001:2026): quiénes son, qué suministran, a qué procesos afectan, con qué
criterios se seleccionaron, si pueden suministrar y cómo los evalúa la
organización. Todos los roles consultan los proveedores, sus evaluaciones y el
PDF. Los administradores y los auditores crean y editan proveedores, y
registran y editan evaluaciones; solo los administradores deciden el estado y
eliminan.

### El proveedor

Pulse **+ Nuevo proveedor** y rellene:

- **Nombre** (obligatorio y único: **Ya existe un proveedor con ese
  nombre.**);
- **NIF** (opcional y único cuando se indica): se guarda en mayúsculas, sin
  espacios ni guiones, y debe tener de 8 a 10 letras o cifras;
- **Persona de contacto**, **Correo electrónico** y **Teléfono**;
- **Tipo de suministro** (obligatorio): **Producto**, **Servicio** o
  **Proceso externalizado**, y **Qué suministra**;
- **Procesos afectados**: los procesos del SGC a los que afecta (para elegir
  varios, mantenga pulsada la tecla Ctrl, o ⌘ en Mac); al editar, los elegidos
  sustituyen a los anteriores;
- **Criterios de selección** (8.4.1);
- **Próxima evaluación** (véase
  [La próxima evaluación](#la-próxima-evaluación));
- **Notas**.

Un proveedor nuevo empieza **Pendiente**; el formulario nunca cambia el
estado.

### Estados y decisiones

| Estado | Significado |
|---|---|
| **Pendiente** | Registrado, todavía sin decisión |
| **Aprobado** | Puede suministrar |
| **Condicionado** | Puede suministrar con las condiciones que indica el motivo |
| **Bloqueado** | No debe suministrar |

- Solo los administradores deciden, en **Decidir sobre el proveedor**, en la
  página del proveedor: **Aprobar**, con un **Motivo (opcional)**, y
  **Condicionar** o **Bloquear**, con un **Motivo** obligatorio (**Indica el
  motivo de la decisión.**).
- La página no ofrece la decisión del estado actual, y el sistema la rechaza
  sin cambiar nada (por ejemplo, **El proveedor ya está aprobado.**).
- Cada decisión registra la fecha de hoy, la persona del administrador y el
  motivo, que la tarjeta **Estado** muestra como **Decidido el**, **Decidido
  por** y **Motivo**. El registro de auditoría guarda las decisiones
  anteriores.
- No se vuelve a **Pendiente**: una decisión se corrige con otra.
- Una evaluación nunca cambia el estado por sí sola: el administrador decide a
  la vista de los resultados. Los agentes de IA nunca deciden el estado.

### Evaluaciones

En **Evaluaciones**, en la página del proveedor, **+ Nueva evaluación**
registra una evaluación:

- **Fecha de la evaluación**: hoy por defecto y nunca futura (**La fecha de la
  evaluación no puede ser futura.**);
- **Evaluador**: una persona activa;
- **Calidad**, **Plazo de entrega**, **Servicio** y **Documentación**, cada
  una de 1 a 5;
- **Comentario**;
- **Próxima evaluación**: posterior a la fecha de la evaluación (**La próxima
  evaluación debe ser posterior a la fecha de la evaluación.**).

La puntuación de una evaluación es la media de sus cuatro notas, con dos
decimales, y su resultado sale de ella:

| Puntuación | Resultado |
|---|---|
| 3,50 o más | **Apto** |
| De 2,50 a 3,49 | **Condicionado** |
| Menos de 2,50 | **No apto** |

- La puntuación y el resultado se calculan; nadie los escribe.
- La última evaluación de un proveedor es la de fecha más reciente; entre dos
  de la misma fecha, la registrada después.
- La **Puntuación media** del proveedor es la media de todas sus
  evaluaciones.
- Cualquier proveedor admite evaluaciones, también uno bloqueado, y una
  evaluación nunca pasa a otro proveedor.
- Los administradores y los auditores editan las evaluaciones; solo los
  administradores las eliminan.

### La próxima evaluación

La **Próxima evaluación** del proveedor sigue a sus evaluaciones:

- al registrar la última evaluación del proveedor con una **Próxima
  evaluación**, esa fecha pasa al proveedor; una evaluación anterior registrada
  más tarde no la cambia;
- después, el proveedor sigue a la última evaluación que tiene próxima
  evaluación: si una edición o una eliminación cambia esa fecha (porque se
  borra o se cambia, la evaluación cambia de fecha o se elimina), el proveedor
  toma la nueva; si no queda ninguna, conserva la suya;
- una fecha escrita en el formulario del proveedor se mantiene mientras
  ninguna evaluación cambie esa fecha.

La evaluación está **vencida** cuando un proveedor que no está bloqueado tiene
la próxima evaluación antes de hoy.

### No conformidades del proveedor

- Una no conformidad con el origen **Proveedor** puede indicar el proveedor
  que la causó en **Proveedor (solo con el origen «Proveedor»)**. Con otro
  origen, el sistema rechaza el proveedor
  (**Solo una no conformidad de origen «Proveedor» puede indicar el
  proveedor.**).
- La página del proveedor enumera sus no conformidades, cada una con su
  enlace, y la de la no conformidad enlaza al proveedor.
- La lista de no conformidades se filtra por **Proveedor**.

### Pantallas

- **La lista**, por nombre, muestra lo que suministra cada proveedor, su
  estado, el último resultado con su puntuación (y la media cuando hay más de
  una evaluación), la próxima evaluación con la etiqueta **Evaluación
  vencida** y sus no conformidades abiertas sobre el total. **Filtros**:
  **Nombre**, **Estado**, **Tipo de suministro**, **Proceso**, **Con
  procesos**, **Último resultado** y **Evaluación vencida**.
- **La página de un proveedor** muestra su **Identificación**, su **Estado**,
  su **Desempeño** (9.1.3 f: evaluaciones, último resultado, puntuación media
  y no conformidades abiertas sobre el total), lo que suministra, sus
  criterios de selección y sus notas, sus **Procesos afectados**, sus **No
  conformidades** y sus **Evaluaciones**.
- **Eliminación** (administradores): un proveedor con evaluaciones o no
  conformidades no se elimina. Su página no muestra **Eliminar** y el sistema
  lo rechaza (**No se puede eliminar un proveedor que tiene evaluaciones;
  bloquéalo en su lugar.**). Tampoco se elimina un proceso vinculado a
  proveedores.
- La tarjeta **Proveedores** del [panel de control](#panel-de-control) cuenta
  los proveedores que requieren atención.

### Lista de proveedores aprobados en PDF

**PDF**, en la lista, genera para todos los roles la lista de proveedores
aprobados que suele pedir una auditoría (`proveedores_AAAA-MM-DD.pdf`):

- la cabecera cuenta los proveedores registrados y por estado, las
  evaluaciones vencidas y la **Puntuación media de los aprobados**, que es la
  media de todas las evaluaciones de los proveedores aprobados;
- **Proveedores aprobados y condicionados**, por nombre: lo que suministra
  cada uno, sus procesos, su estado con la fecha y el motivo, la fecha, la
  puntuación y el resultado de su última evaluación, su puntuación media con
  el número de evaluaciones, su próxima evaluación (y si está vencida) y sus
  no conformidades abiertas;
- **Proveedores pendientes y bloqueados**, por nombre y con los mismos datos.

## Revisión por la dirección

**Revisión por la dirección** (`/revisiones-direccion/`, apartado 9.3 de ISO
9001:2026) registra cada revisión del SGC por la alta dirección: qué se
presentó, qué se concluyó y qué se decidió. Los administradores y los
auditores consultan y redactan las revisiones y sus acciones; solo los
administradores las celebran, las cierran y las eliminan. Los usuarios con
rol Operativo no tienen acceso.

### La revisión

Pulse **+ Nueva revisión** y rellene:

- **Fecha de la reunión**;
- **Periodo revisado: desde** y **hasta**: el periodo termina en la fecha de
  la reunión o antes (**El periodo revisado debe terminar en la fecha de la
  reunión o antes.**);
- **Asistentes** (personas);
- un comentario para cada entrada del apartado 9.3.2, de a) a f) (el
  comentario de c) cubre también a los proveedores externos).

### Estados

| Estado | Qué se puede cambiar | Quién lo cambia |
|---|---|---|
| **Borrador** | La reunión, el periodo, los asistentes y los comentarios: **Editar** | Un administrador pulsa **Celebrar revisión** |
| **Celebrada** | Solo los comentarios y las conclusiones: **Comentarios y conclusiones** | Un administrador pulsa **Cerrar revisión** |
| **Cerrada** | Nada; solo el seguimiento de sus acciones | Estado final: no hay vuelta atrás |

- **Celebrarla** requiere una fecha de reunión que no sea futura y al menos un
  asistente (**Indica al menos un asistente antes de celebrar la
  revisión.**). Registra la persona del administrador como **Celebrada por**
  y congela las entradas.
- **Conclusiones.** Una vez celebrada la revisión, **Comentarios y
  conclusiones** recoge los comentarios y las **Conclusiones sobre la
  conveniencia, adecuación y eficacia del SGC**.
- **Cerrarla** requiere las conclusiones (**Escribe las conclusiones antes de
  cerrar la revisión.**) y registra la fecha de cierre.
- **Eliminarla** (administradores) solo es posible en un borrador sin
  acciones.

### Entradas (9.3.2)

La página de la revisión enumera cada entrada con cifras obtenidas de los
registros del periodo, junto a su comentario:

- **a)** las acciones de revisiones anteriores: abiertas al final del
  periodo, por estado, completadas después del periodo, vencidas y
  completadas en el periodo;
- **b)** las cuestiones externas e internas: las nuevas, las modificadas y
  las superadas en el periodo, con su lista, y cuántas hay vigentes, cuántas
  son pertinentes y cuántas se relacionan con el cambio climático (todas y
  solo las pertinentes); las partes interesadas; la versión vigente del
  alcance del SGC y su fecha de aprobación; y los riesgos y oportunidades:
  registrados, nuevos y modificados en el periodo, los riesgos por nivel con
  los altos o críticos abiertos y las oportunidades por prioridad;
- **c)** la satisfacción del cliente (encuestas y puntuación media), los
  objetivos de la calidad (que cumplen la meta, que no la cumplen, sin
  medición, con una medición vencida, por estado), el desempeño de los
  procesos (registros de indicadores de auditoría), las no conformidades
  (detectadas, por gravedad y origen; cerradas; y eficacia de las acciones
  correctivas), las auditorías (en el periodo, completadas, hallazgos por
  tipo) y los proveedores externos (9.1.3 f): los proveedores por su estado
  actual (el sistema no guarda el historial de los estados), los que tienen la
  evaluación vencida en la fecha de corte, las evaluaciones del periodo con su
  puntuación media y sus resultados, los proveedores con una evaluación **No
  apto** en el periodo, y las no conformidades de origen **Proveedor**
  detectadas en el periodo, con los diez proveedores más citados; y los
  cambios en el SGC (6.3): los propuestos, aprobados, implantados, con la
  eficacia revisada (eficaces y no eficaces) y cancelados en el periodo, cada
  uno contado por la fecha de esa etapa; los implantados o revisados en el
  periodo, con su estado, sus fechas y su resultado, del más reciente al más
  antiguo; y los aprobados que en la fecha de corte estaban fuera de plazo
  sin implantar ni cancelar, con su responsable, del más atrasado al menos.
  Cada lista muestra como mucho diez y avisa cuando hay más (**Se muestran los
  10 más recientes de …**);
- **d)** la capacitación: sesiones, horas y temas;
- **e)** la eficacia de las acciones tomadas para abordar los riesgos y las
  oportunidades: cada acción registra la suya (véase
  [Acciones](#acciones)), pero la revisión todavía no la resume, así que la
  cubre el comentario;
- **f)** las mejoras implantadas en el periodo.

Las cifras cuentan el periodo hasta su final, o hasta hoy si el final es
posterior (la **Fecha de corte**); los recuentos de cuestiones vigentes y la
versión del alcance son los del momento en que se calculan. Las revisiones
celebradas antes de que existiera el registro de proveedores no muestran la
parte de los proveedores, ni las celebradas antes del registro de cuestiones
del contexto muestran las cuestiones ni el alcance, ni las celebradas antes
del registro de cambios muestran los cambios. En un borrador se calculan
en el momento y
se marcan como **Datos provisionales**. Celebrar la revisión las congela, de
modo que la revisión y su acta siguen mostrando lo que se presentó aunque los
registros cambien después.

### Acciones (9.3.3)

En **Salidas: acciones**, **+ Nueva acción** registra una decisión:

- **Tipo**: **Oportunidad de mejora**, **Cambio en el SGC** o **Necesidad de
  recursos**;
- **Descripción**, **Responsable** y **Fecha límite**;
- **Estado**: **Pendiente**, **En curso**, **Completada** o **Cancelada**;
- **Fecha de finalización** (solo cuando está completada) y **Resultado**.

**Reglas:**

- Las acciones se añaden, editan y eliminan mientras la revisión es un
  borrador o está celebrada. Una vez cerrada, **Seguimiento** solo cambia el
  estado, la fecha de finalización y el resultado.
- Una acción completada necesita su fecha de finalización: nunca futura ni
  anterior a la fecha de la revisión. La fecha de la revisión no puede pasar
  de la de una acción completada.
- Una acción está vencida cuando sigue **Pendiente** o **En curso** después de
  su fecha límite.

### Pantallas

- **La lista** se filtra por **Año** y **Estado**, y cuenta los asistentes de
  cada revisión y sus acciones abiertas, vencidas y totales.
- **La página de la revisión** muestra la **Reunión**, las conclusiones, las
  entradas con sus comentarios y las acciones con una etiqueta si están
  vencidas.
- **Registro de acciones** (`/revisiones-direccion/acciones`) enumera las
  acciones de todas las revisiones y se filtra por **Estado**,
  **Responsable**, **Tipo** y **Vencidas**.
- La tarjeta **Acciones de la revisión por la dirección** del panel de
  control enumera las acciones vencidas.

### Acta en PDF

**Acta PDF**, en la página de la revisión, genera el acta:

- la reunión: fecha, periodo, estado, asistentes, quién la celebró, la fecha
  de cierre y la fecha de corte de los datos;
- cada entrada con sus cifras y su comentario, marcada **BORRADOR — datos
  provisionales** mientras la revisión es un borrador;
- las conclusiones;
- las acciones: tipo, descripción con los cambios en el SGC que origina y su
  estado, responsable, fecha límite (y si está vencida), estado, fecha de
  finalización y resultado;
- la fecha en que se generó el acta.

## Otros registros

Estos registros son anteriores a los módulos descritos más arriba. Todos
tienen pantallas, y el servidor MCP (véanse
[Agentes y tokens de API](#agentes-y-tokens-de-api) y [`mcp.md`](mcp.md)) los
consulta y los escribe con las mismas reglas de rol.

| Registro | Apartado | Dónde | Campos |
|---|---|---|---|
| **Capacitaciones** | 7.2 | Menú **Personas y competencia** | **Tema**, **Fecha**, **Personal** y **Persona**, **Duración en Horas**, **Evaluación Final** |
| **Mejoras** | 10.3 | Menú **Calidad** | **No conformidad** (obligatoria), **Acción correctiva**, **Acción preventiva**; la fecha **Implantada** se fija al registrar la mejora |
| **Satisfacción** | 9.1.2 | Menú **Clientes** | **Cliente**, **Fecha de Encuesta**, **Puntuación (1-10)**, **Comentarios** |

Notas:

- Los indicadores de auditoría, las capacitaciones y las mejoras alimentan
  las entradas de la revisión por la dirección.
- **Capacitaciones** se filtra por **Tema**, **Fecha** y **Personal**, y cada
  sesión tiene un **PDF** (**Registro de capacitación**). Una competencia
  acreditada puede citar una sesión como evidencia.
- **Satisfacción** se filtra por **Cliente** y **Puntuación mínima**, y cada
  encuesta tiene un **PDF** (**Encuesta de satisfacción**).
- **Mejoras** muestra las mejoras de diez en diez.
- Todos los roles registran capacitaciones, mejoras y encuestas; solo los
  administradores las eliminan.

### Recursos de capacitación

**Recursos de capacitación** (`/recursos-formacion/`), en el menú **Personas y
competencia**, registra los recursos necesarios (apartado 7.1). Cada recurso
tiene **Recurso necesario** (obligatorio), **Requiere capacitación del
personal** y **Descripción y documentación**. La lista los ordena por recurso,
y la página de cada uno muestra sus campos. Todos los roles los consultan; los
administradores y los auditores los crean y los editan; solo los
administradores los eliminan, desde la página del recurso.

### Indicadores de auditoría

**Indicadores de auditoría** (`/indicadores-auditoria/`), en el menú
**Calidad**, es un registro heredado de resultados de auditoría y de
indicadores de desempeño (apartado 9.1). Cada indicador tiene **Área
auditada** (obligatoria, hasta 50 caracteres), **Fecha de la auditoría**
(obligatoria; un indicador nuevo empieza con la fecha de hoy), **Resultado**,
**Acción correctiva** e **Indicador de desempeño**.

- La lista muestra primero los más recientes y recuerda que es un registro
  heredado: los indicadores nuevos de los procesos se registran como
  [objetivos de la calidad](#objetivos-de-la-calidad), con su meta y sus
  mediciones.
- Todos los roles los consultan; los administradores y los auditores los crean
  y los editan; solo los administradores los eliminan, desde la página del
  indicador.

## Informes y notificaciones por correo electrónico

### Informe mensual de calidad

El **Informe mensual de calidad** (apartado 9.1) es un PDF que se envía por
correo electrónico a los administradores el día 1 de cada mes. Abarca el mes
natural que acaba de terminar y no está disponible desde las pantallas web.

La cabecera indica el periodo del informe (por ejemplo, **Periodo: septiembre
de 2026**) y, en **Generado el**, el día en que se generó: el día 1 en que se
envía, no el último día del mes.

Cada cifra cuenta:

| Cifra | Qué cuenta |
|---|---|
| **Auditorías** | Las auditorías con fecha en el mes, en cualquier estado |
| **No conformidades** | Las no conformidades detectadas en el mes |
| **Satisfacción media** | La puntuación media de las encuestas con fecha en el mes, sobre 10 (0 si no hay encuestas) |
| **Capacitaciones** | Las sesiones de capacitación con fecha en el mes |
| **No conformidades por estado** | Las no conformidades del mes, según su estado en el momento de generar el informe |
| **Acciones correctivas verificadas** | Las acciones correctivas cuya fecha de verificación cae en el mes, separadas en **Eficaces** y **No eficaces** |
| **Auditorías completadas** | Las auditorías con fecha en el mes que están **Completada** |
| **Hallazgos de auditoría por tipo** | Los hallazgos de las auditorías con fecha en el mes, por tipo |
| **Mediciones registradas** | Las mediciones de objetivos de la calidad con fecha en el mes |
| **Objetivos activos** | Los objetivos que están **Activo** al generar el informe y que empezaron, como tarde, el último día del mes |
| **Objetivos activos que cumplen la meta** | De ellos, los que cumplen su meta con las mediciones hasta el último día del mes |
| **Objetivos activos con medición vencida** | De ellos, los que tienen una medición vencida el último día del mes |

Un objetivo cerrado después de terminar el mes ya no cuenta como activo,
porque el sistema no guarda la fecha de los cambios de estado.

### Notificaciones por correo electrónico

El servidor envía cuatro correos programados, a las horas indicadas abajo en
la zona horaria de la organización. Se envían a los usuarios activos con
dirección de correo electrónico; los demás se omiten. Los correos de
auditorías y de documentos no se envían cuando no hay nada que enumerar.
La parte del servidor (Redis y Celery) se describe en la
[guía de operaciones](operaciones.md#redis-y-celery).

| Asunto | Destinatarios | Cuándo | Contenido |
|---|---|---|---|
| **Recordatorio de Auditorías Próximas** | Usuarios con el rol **Auditor** | Todos los días a las 07:00 | Las auditorías **Pendiente** o **En Proceso** con fecha desde hoy hasta 7 días después, con área y fecha |
| **Reporte de Auditorías Pendientes** | Usuarios con el rol **Administrador** | Los lunes a las 08:00 | Todas las auditorías en estado **Pendiente**, sea cual sea su fecha, con número, área y fecha |
| **Reporte Mensual del Sistema de Gestión de Calidad** | Usuarios con el rol **Administrador** | El día 1 de cada mes a las 08:00 | El [informe mensual](#informe-mensual-de-calidad) del mes terminado, adjunto como `reporte-calidad-YYYY-MM.pdf` |
| **Revisiones periódicas de documentos pendientes** | Los administradores y los propietarios de documentos | Los lunes a las 08:00 | Los documentos en uso cuya fecha de próxima revisión ha llegado, con código, título y fecha de revisión |

El correo de revisión de documentos se prepara para cada destinatario:

- Los administradores reciben la lista completa, con el propietario de cada
  documento.
- Cada propietario recibe sus propios documentos a través de la cuenta de
  usuario vinculada a su persona (un usuario con rol Operativo, solo los
  documentos vigentes).
- Los propietarios sin usuario vinculado no reciben nada.

Los correos de restablecimiento de contraseña no son programados: se envían
cuando alguien solicita un enlace o cuando un administrador lo envía (véase
[Inicio de sesión y cuenta](#inicio-de-sesión-y-cuenta)).

## Términos

| Término | Significado |
|---|---|
| Acta | El PDF que recoge una revisión por la dirección |
| Administrador, Auditor, Operativo | Los tres roles de usuario; deciden qué puede hacer cada cuenta |
| Alcance del SGC | Los límites y la aplicabilidad del SGC (4.3), guardados por versiones: borrador, vigente y obsoleta |
| Auditor jefe | La persona que dirige una auditoría: **Auditor (persona)** |
| Cambio en el SGC | Un cambio planificado en el sistema de gestión de la calidad (6.3), con su plan, sus tareas, su aprobación, su implantación y la revisión de su eficacia |
| Capacitación | Una sesión de formación registrada; puede servir como evidencia de competencia |
| Celebrada | Una revisión por la dirección ya realizada; sus entradas quedan congeladas |
| Competencia acreditada | La evidencia de que una persona cumple un requisito de competencia |
| Competencia requerida | Lo que exige un rol del SGC: educación, formación, habilidad o experiencia |
| Cuestión del contexto | Un factor externo o interno que puede afectar a los resultados previstos del SGC (4.1) |
| De baja | Un documento retirado; no se elimina y queda de solo lectura |
| Fecha de corte | La fecha hasta la que cuentan los datos de una revisión por la dirección |
| Fuera de plazo | Un cambio en el SGC aprobado cuya fecha prevista de implantación ya ha pasado |
| Línea de reporte | Los roles por encima de un rol, desde el que lo dirige directamente hasta la cúspide |
| Medición vencida | Un objetivo activo cuya siguiente medición debía haberse registrado ya |
| Nivel, nivel residual | La gravedad de un riesgo según su probabilidad por su impacto, antes y después de sus acciones |
| Obsoleto | Una revisión de un documento sustituida o retirada; se conserva como evidencia |
| Persona | Quien trabaja bajo el SGC; no es lo mismo que una cuenta de usuario |
| Pertinente | Una cuestión del contexto o una parte interesada que influye en el SGC; una cuestión no pertinente justifica por qué |
| Prioridad | La importancia de una oportunidad según su beneficio por su viabilidad |
| Proceso relacionado | Un proceso con el que interactúa otro; la relación vale en ambos sentidos |
| Propietario | La persona responsable de un documento y de su revisión periódica |
| Revisión (de un documento) | Cada versión numerada de su texto; distinta de la revisión periódica y de la revisión por la dirección |
| Revisión vencida | Un riesgo o una oportunidad abiertos, una cuestión del contexto vigente o una parte interesada cuya próxima revisión ya ha pasado |
| SGC | Sistema de gestión de la calidad |
| Sin valorar | Un riesgo o una oportunidad que aún no tiene valoración |
| Superada | Una cuestión del contexto que ya no aplica; se conserva como evidencia |
| Token de API | La credencial con la que un agente de IA actúa en nombre de un usuario |
| Vigente | La revisión de un documento que está en vigor; la única que ve el rol Operativo |
