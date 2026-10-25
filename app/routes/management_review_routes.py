# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""Management review screens (ISO 9001 clause 9.3): adapters over ``management_reviews``.

Task MR-2 of ``management-review``. Administrators and auditors list and read
reviews and their actions, create and edit drafts, write the comments and
conclusions of a held review, and add and follow up actions; administrators
hold and close reviews (``may_decide``, decision MR4) and delete drafts without
actions and the actions of a review that is not closed (MR5, MR6). Other roles
are refused (policy ``MANAGEMENT_REVIEW``).

- The list filters by the meeting's year and the state and counts each
  review's attendees and its actions: open, overdue on ``local_today`` and in
  all (``action_counts``).
- A review's page shows the inputs of 9.3.2 input by input
  (``input_sections``), each next to its comment: live with a provisional
  note while a draft, as frozen once held. Its actions carry an overdue badge.
- A draft is edited with its meeting, period, attendees and comments; a held
  review only with its comments and conclusions (``/conclusiones``). A form
  asked for in another state returns to the review's page with the reason.
- Actions live under their review; one asked for under another review is a
  404. Once the review is closed, an action's form keeps only the follow-up
  fields (state, completion date, result) and shows the rest.
- The action register (``/acciones``) filters by state, owner, type and
  ``vencida`` (``1`` or ``0``) and links each action to its review; a filter
  value that names nothing is ignored.
- The minutes (``/<id>/pdf``, task MR-3) have the page's access and content:
  the meeting with its attendees by name, each input with its figures and its
  comment (live and marked as a draft until held), the conclusions and the
  actions with their follow-up. A PDF that cannot be rendered is logged and
  flashed on the review's page.

A service ``ValidationError`` or ``Conflict`` is rolled back and flashed on the
re-rendered form, which keeps what was typed; a refused hold, close or delete
is flashed on the review's page.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from ..audit_notifications import local_today
from ..extensions import db
from ..forms import (
    CHOOSE_PERSON, REVIEW_INPUTS, AccionRevisionForm, ConclusionesRevisionForm,
    RevisionDireccionForm, number_text, person_choices,
)
from ..models import (
    AccionRevision, CategoriaCuestion, EficaciaCambio, EstadoAccionRevision, EstadoCambio,
    EstadoCuestion, EstadoObjetivo, EstadoOportunidad, EstadoProveedor, EstadoRevisionDireccion,
    EstadoRiesgo, GravedadNoConformidad, NivelRiesgo, OrigenNoConformidad, PrioridadOportunidad,
    ResultadoEvaluacion, RevisionDireccion, TipoAccionRevision, TipoContexto, TipoHallazgo,
)
from ..services import management_reviews as mr
from ..services import people
from ..services.actor import Actor
from ..services.errors import NotFound, PermissionDenied
from ..utils.pdf import pdf_response, render_pdf
from ..utils.permissions import can, require_permission
from ..utils.web_actor import current_actor
from .auditoria_routes import committed
from .change_routes import citing_changes
from .people_routes import form_data, saved

bp = Blueprint('revision_direccion', __name__, url_prefix='/revisiones-direccion')

REVIEW_FIELDS = ('fecha', 'periodo_desde', 'periodo_hasta', 'asistente_ids', *mr.COMMENT_FIELDS)
HELD_FIELDS = (*mr.COMMENT_FIELDS, 'conclusiones')
FOLLOW_UP = ('estado', 'fecha_completada', 'resultado')
ACTION_FIELDS = ('tipo', 'descripcion', 'responsable_id', 'fecha_limite', *FOLLOW_UP)
REVIEW_STATES = frozenset(member.name for member in EstadoRevisionDireccion)
CONCLUSIONS_WHEN_HELD = 'Las conclusiones se escriben una vez celebrada la revisión.'
BORRADOR, CELEBRADA, CERRADA = EstadoRevisionDireccion


def may_decide() -> bool:
    """Whether the logged-in user may hold and close reviews (administrators, MR4)."""
    return (can('update', 'management_review')
            and current_actor().role in mr.TRANSITION_ROLES)


def _review_page(review_id: int) -> ResponseReturnValue:
    return redirect(url_for('revision_direccion.show_review', review_id=review_id))


def _refused(review_id: int, message: str) -> ResponseReturnValue:
    flash(message, 'danger')
    return _review_page(review_id)


# -- the inputs of 9.3.2, labelled -----------------------------------------------------


def _at(data: Any, *path: str) -> Any:
    """``data[path[0]][path[1]]...``, or ``None`` when a step is missing."""
    for key in path:
        data = data.get(key) if isinstance(data, Mapping) else None
    return data


def _day(text: Any) -> str:
    """An ISO date of the inputs as ``dd/mm/yyyy``, or a dash."""
    try:
        return date.fromisoformat(text).strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return '—'


def _number(value: Any) -> str:
    """A count, average or decimal text of the inputs in Spanish, or a dash."""
    if value is None:
        return '—'
    try:
        return number_text(Decimal(str(value)))
    except InvalidOperation:
        return str(value)


class _CambioCuestion(Enum):
    """How a listed context issue changed in the period (``contexto.cuestiones``, OC5)."""

    nueva = 'Nueva'
    modificada = 'Modificada'


def _yes_no(value: Any) -> str:
    """A flag of the inputs as «Sí» or «No», or a dash when missing."""
    return '—' if value is None else ('Sí' if value else 'No')


class _TipoAnterior(Enum):
    """The record types of ``contexto.riesgos_oportunidades``, frozen before RO-2 (RO7)."""

    Riesgo = 'Riesgo'
    Oportunidad = 'Oportunidad'


_UNSET = {'sin_indicar': 'Sin indicar', 'sin_valorar': 'Sin valorar'}


def _member(enum_cls: Any, name: Any) -> str:
    """The Spanish label of a member name of the inputs (or of ``_UNSET``)."""
    if name in _UNSET:
        return _UNSET[name]
    return enum_cls[name].value if name in enum_cls.__members__ else str(name or '—')


def _block(titulo: str, cifras: list, columnas: tuple = (), filas: Any = (),
           nota: str | None = None) -> dict:
    return {'titulo': titulo, 'cifras': cifras, 'columnas': columnas, 'filas': list(filas),
            'nota': nota}


def _cut(datos: Mapping[str, Any], total: tuple[str, ...], lista: tuple[str, ...],
         which: str) -> str | None:
    """Say so when the list at ``lista`` holds fewer items than its count at ``total``."""
    listed, counted = _at(datos, *lista), _at(datos, *total)
    if not isinstance(listed, list) or not isinstance(counted, int) or counted <= len(listed):
        return None
    return f'Se muestran los {len(listed)} {which} de {counted}.'


def input_sections(datos: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The inputs (``management_reviews.inputs``) as labelled sections a) to f) of 9.3.2.

    Each section has its ``titulo`` (``REVIEW_INPUTS``), its ``comentario``
    field and ``bloques`` of figures (label, text) and of optional rows under
    ``columnas``. A missing key shows as a dash, so inputs frozen with an older
    shape still render; reviews held before RO-2 of ``risks-opportunities`` show
    their single ``riesgos_oportunidades`` count by type instead of the risk and
    opportunity registers (RO7). The external providers (input c, EP-3 of
    ``external-providers``) show only when the inputs hold ``proveedores``: reviews
    held before it froze none. Likewise the context issues and the scope in force
    (input b, OC5 of ``organization-context``) show only when ``contexto`` holds
    ``cuestiones`` and ``alcance``, and the changes to the QMS (input c, CP6 of
    ``change-planning``) only when the inputs hold ``cambios``. A block's ``nota``
    says when its list was cut at ``LIST_LIMIT``.
    """
    def n(*path: str) -> str:
        return _number(_at(datos, *path))

    def rows(*path: str) -> list:
        found = _at(datos, *path)
        return found if isinstance(found, list) else []

    def by(prefix: str, enum_cls: Any, *path: str) -> list[tuple[str, str]]:
        counts = _at(datos, *path)
        return [(f'{prefix}: {_member(enum_cls, key)}', _number(value))
                for key, value in (counts.items() if isinstance(counts, Mapping) else ())]

    previas = _block('Acciones de revisiones anteriores', [
        ('Abiertas al cierre del periodo', n('acciones_previas', 'abiertas')),
        ('Pendientes', n('acciones_previas', 'por_estado', 'pendiente')),
        ('En curso', n('acciones_previas', 'por_estado', 'en_curso')),
        ('Completadas después del cierre', n('acciones_previas', 'completadas_despues')),
        ('Vencidas al cierre', n('acciones_previas', 'vencidas')),
        ('Completadas en el periodo', n('acciones_previas', 'completadas_en_periodo')),
    ], ('Revisión', 'Tipo', 'Descripción', 'Responsable', 'Fecha límite', 'Estado'), (
        (_day(a.get('revision_fecha')), _member(TipoAccionRevision, a.get('tipo')),
         a.get('descripcion'), a.get('responsable'),
         _day(a.get('fecha_limite')) + (' (vencida)' if a.get('vencida') else ''),
         _member(EstadoAccionRevision, a.get('estado')))
        for a in rows('acciones_previas', 'lista')))
    partes = _block('Partes interesadas', [
        ('Registradas', n('contexto', 'partes_interesadas', 'total')),
        ('Nuevas en el periodo', n('contexto', 'partes_interesadas', 'nuevas')),
        ('Modificadas en el periodo', n('contexto', 'partes_interesadas', 'modificadas')),
    ], ('Parte interesada',), (
        (name,) for name in rows('contexto', 'partes_interesadas', 'lista')))
    riesgos = _block('Riesgos', [
        ('Riesgos registrados', n('contexto', 'riesgos', 'total')),
        *by('Nivel', NivelRiesgo, 'contexto', 'riesgos', 'por_nivel'),
        ('Riesgos nuevos en el periodo', n('contexto', 'riesgos', 'nuevos')),
        ('Riesgos modificados en el periodo', n('contexto', 'riesgos', 'modificados')),
    ], ('Riesgo nuevo o modificado', 'Nivel', 'Estado'), (
        (r.get('titulo'), _member(NivelRiesgo, r.get('nivel')),
         _member(EstadoRiesgo, r.get('estado')))
        for r in rows('contexto', 'riesgos', 'lista')))
    altos = _block('Riesgos altos o críticos abiertos', [
        ('Abiertos con nivel alto o crítico', n('contexto', 'riesgos', 'altos_abiertos')),
    ], ('Riesgo', 'Nivel', 'Responsable', 'Estado'), (
        (r.get('titulo'), _member(NivelRiesgo, r.get('nivel')), r.get('responsable'),
         _member(EstadoRiesgo, r.get('estado')))
        for r in rows('contexto', 'riesgos', 'altos_abiertos_lista')))
    oportunidades = _block('Oportunidades', [
        ('Oportunidades registradas', n('contexto', 'oportunidades', 'total')),
        *by('Prioridad', PrioridadOportunidad, 'contexto', 'oportunidades', 'por_prioridad'),
        ('Oportunidades nuevas en el periodo', n('contexto', 'oportunidades', 'nuevas')),
        ('Oportunidades modificadas en el periodo',
         n('contexto', 'oportunidades', 'modificadas')),
    ], ('Oportunidad nueva o modificada', 'Prioridad', 'Estado'), (
        (o.get('titulo'), _member(PrioridadOportunidad, o.get('prioridad')),
         _member(EstadoOportunidad, o.get('estado')))
        for o in rows('contexto', 'oportunidades', 'lista')))
    anteriores = _block('Riesgos y oportunidades', [
        *by('Registrados', _TipoAnterior, 'contexto', 'riesgos_oportunidades', 'por_tipo'),
        ('Nuevos en el periodo', n('contexto', 'riesgos_oportunidades', 'nuevos')),
        ('Modificados en el periodo', n('contexto', 'riesgos_oportunidades', 'modificados')),
    ], ('Tipo', 'Descripción'), (
        (_member(_TipoAnterior, r.get('tipo')), r.get('descripcion'))
        for r in rows('contexto', 'riesgos_oportunidades', 'lista')))
    cuestiones = [_block('Cuestiones externas e internas', [
        ('Cuestiones nuevas en el periodo', n('contexto', 'cuestiones', 'nuevas')),
        ('Cuestiones modificadas en el periodo', n('contexto', 'cuestiones', 'modificadas')),
        ('Cuestiones superadas en el periodo', n('contexto', 'cuestiones', 'superadas')),
        ('Cuestiones vigentes', n('contexto', 'cuestiones', 'vigentes')),
        ('Vigentes pertinentes', n('contexto', 'cuestiones', 'pertinentes')),
        ('Vigentes sobre el cambio climático', n('contexto', 'cuestiones', 'cambio_climatico')),
        ('Pertinentes sobre el cambio climático',
         n('contexto', 'cuestiones', 'cambio_climatico_pertinentes')),
    ], ('Cuestión nueva o modificada', 'Cambio', 'Tipo', 'Categoría', 'Pertinente',
        'Cambio climático', 'Estado'), (
        (c.get('titulo'), _member(_CambioCuestion, c.get('cambio')),
         _member(TipoContexto, c.get('tipo')), _member(CategoriaCuestion, c.get('categoria')),
         _yes_no(c.get('pertinente')), _yes_no(c.get('cambio_climatico')),
         _member(EstadoCuestion, c.get('estado')))
        for c in rows('contexto', 'cuestiones', 'lista')))
    ] if isinstance(_at(datos, 'contexto', 'cuestiones'), Mapping) else []
    alcance = [_block('Alcance del SGC', [
        ('Versión vigente del alcance', n('contexto', 'alcance', 'version')),
        ('Fecha de aprobación del alcance', _day(_at(datos, 'contexto', 'alcance',
                                                     'fecha_aprobacion'))),
    ])] if isinstance(_at(datos, 'contexto', 'alcance'), Mapping) else []
    frozen_before_registers = _at(datos, 'contexto', 'riesgos_oportunidades') is not None
    contexto = [*cuestiones, partes, *alcance,
                *([anteriores] if frozen_before_registers else [riesgos, altos, oportunidades])]
    satisfaccion = _block('Satisfacción del cliente', [
        ('Encuestas en el periodo', n('satisfaccion', 'encuestas')),
        ('Puntuación media', n('satisfaccion', 'media')),
    ])
    objetivos = _block('Objetivos de la calidad', [
        ('Objetivos del periodo', n('objetivos', 'total')),
        ('En meta', n('objetivos', 'en_meta')),
        ('Fuera de meta', n('objetivos', 'fuera_de_meta')),
        ('Sin medición', n('objetivos', 'sin_medicion')),
        ('Con medición vencida', n('objetivos', 'medicion_vencida')),
        *by('Estado', EstadoObjetivo, 'objetivos', 'por_estado'),
    ], ('Objetivo fuera de meta', 'Último valor', 'Fecha', 'Meta'), (
        (o.get('titulo'), _number(o.get('ultimo_valor')), _day(o.get('ultima_fecha')),
         f"{o.get('sentido') or ''} {_number(o.get('meta'))}".strip())
        for o in rows('objetivos', 'fuera_de_meta_lista')))
    indicadores = _block('Desempeño de los procesos', [
        ('Registros de indicadores en el periodo', n('indicadores', 'total')),
    ], ('Área', 'Fecha', 'Indicador', 'Resultado'), (
        (i.get('area'), _day(i.get('fecha')), i.get('indicador'), i.get('resultado'))
        for i in rows('indicadores', 'lista')))
    no_conformidades = _block('No conformidades y acciones correctivas', [
        ('Detectadas en el periodo', n('no_conformidades', 'abiertas_en_periodo')),
        *by('Gravedad', GravedadNoConformidad, 'no_conformidades', 'por_gravedad'),
        *by('Origen', OrigenNoConformidad, 'no_conformidades', 'por_origen'),
        ('Cerradas en el periodo', n('no_conformidades', 'cerradas_en_periodo')),
        ('Acciones correctivas eficaces',
         n('no_conformidades', 'acciones_correctivas', 'eficaces')),
        ('Acciones correctivas no eficaces',
         n('no_conformidades', 'acciones_correctivas', 'no_eficaces')),
    ])
    auditorias = _block('Resultados de las auditorías', [
        ('Auditorías en el periodo', n('auditorias', 'en_periodo')),
        ('Completadas', n('auditorias', 'completadas')),
        *by('Hallazgos', TipoHallazgo, 'auditorias', 'hallazgos_por_tipo'),
    ], ('Auditoría completada', 'Fecha'), (
        (a.get('area'), _day(a.get('fecha'))) for a in rows('auditorias', 'completadas_lista')))
    capacitaciones = _block('Formación', [
        ('Capacitaciones en el periodo', n('capacitaciones', 'total')),
        ('Horas de formación', n('capacitaciones', 'horas')),
    ], ('Tema',), ((tema,) for tema in rows('capacitaciones', 'temas')))
    proveedores = [_block('Proveedores externos', [
        *by('Estado actual', EstadoProveedor, 'proveedores', 'por_estado'),
        ('Evaluaciones vencidas al cierre', n('proveedores', 'evaluaciones_vencidas')),
        ('Evaluaciones en el periodo', n('proveedores', 'evaluaciones', 'total')),
        ('Puntuación media de las evaluaciones', n('proveedores', 'evaluaciones', 'media')),
        *by('Resultado', ResultadoEvaluacion, 'proveedores', 'evaluaciones', 'por_resultado'),
        ('Proveedores no aptos en el periodo', n('proveedores', 'no_aptos')),
    ], ('Proveedor no apto', 'Fecha', 'Puntuación', 'Estado actual'), (
        (p.get('nombre'), _day(p.get('fecha')), _number(p.get('puntuacion')),
         _member(EstadoProveedor, p.get('estado')))
        for p in rows('proveedores', 'no_aptos_lista'))), _block(
        'No conformidades de proveedores', [
            ('Detectadas en el periodo con origen proveedor',
             n('proveedores', 'no_conformidades', 'total')),
            ('Sin proveedor indicado', n('proveedores', 'no_conformidades', 'sin_proveedor')),
        ], ('Proveedor', 'No conformidades'), (
            (p.get('nombre'), _number(p.get('total')))
            for p in rows('proveedores', 'no_conformidades', 'por_proveedor')))
    ] if isinstance(_at(datos, 'proveedores'), Mapping) else []
    cambios = [_block('Cambios en el SGC', [
        ('Cambios propuestos en el periodo', n('cambios', 'propuestos')),
        ('Cambios aprobados en el periodo', n('cambios', 'aprobados')),
        ('Cambios implantados en el periodo', n('cambios', 'implantados')),
        ('Con la eficacia revisada en el periodo', n('cambios', 'revisados')),
        ('Revisados como eficaces', n('cambios', 'eficaces')),
        ('Revisados como no eficaces', n('cambios', 'no_eficaces')),
        ('Cambios cancelados en el periodo', n('cambios', 'cancelados')),
        ('Implantados o revisados en el periodo', n('cambios', 'implantados_o_revisados')),
    ], ('Cambio implantado o revisado', 'Estado', 'Implantación', 'Revisión de la eficacia',
        'Resultado'), (
        (c.get('titulo'), _member(EstadoCambio, c.get('estado')),
         _day(c.get('fecha_implantacion')), _day(c.get('fecha_eficacia')),
         _member(EficaciaCambio, c.get('eficacia')))
        for c in rows('cambios', 'implantados_o_revisados_lista')),
        _cut(datos, ('cambios', 'implantados_o_revisados'),
             ('cambios', 'implantados_o_revisados_lista'), 'más recientes')),
        _block('Cambios aprobados fuera de plazo al cierre', [
            ('Aprobados fuera de plazo al cierre', n('cambios', 'fuera_de_plazo')),
        ], ('Cambio', 'Responsable', 'Aprobado', 'Implantación prevista', 'Estado actual'), (
            (c.get('titulo'), c.get('responsable'), _day(c.get('fecha_aprobacion')),
             _day(c.get('fecha_prevista')), _member(EstadoCambio, c.get('estado')))
            for c in rows('cambios', 'fuera_de_plazo_lista')),
            _cut(datos, ('cambios', 'fuera_de_plazo'), ('cambios', 'fuera_de_plazo_lista'),
                 'más atrasados')),
    ] if isinstance(_at(datos, 'cambios'), Mapping) else []
    mejoras = _block('Mejoras', [
        ('Mejoras implantadas en el periodo', n('mejoras', 'total')),
    ], ('Mejora', 'Fecha'), ((m.get('descripcion'), _day(m.get('fecha')))
                             for m in rows('mejoras', 'lista')))
    blocks = {
        'comentario_acciones_previas': [previas],
        'comentario_contexto': contexto,
        'comentario_desempeno': [satisfaccion, objetivos, indicadores, no_conformidades,
                                 auditorias, *proveedores, *cambios],
        'comentario_recursos': [capacitaciones],
        'comentario_riesgos': [],  # no register: the comment covers it
        'comentario_mejora': [mejoras],
    }
    return [{'titulo': titulo, 'comentario': field, 'bloques': blocks[field]}
            for field, titulo in REVIEW_INPUTS.items()]


# -- reviews ---------------------------------------------------------------------------


@bp.route('/', methods=['GET'])
@login_required
@require_permission('read', 'management_review')
def list_reviews() -> ResponseReturnValue:
    """Reviews matching the year and state filters, with attendees and actions counted."""
    actor = current_actor()
    estado = request.args.get('estado', '')
    filters = {'anio': request.args.get('anio', type=int),
               'estado': estado if estado in REVIEW_STATES else None}
    applied = sum(value is not None for value in filters.values())
    every = mr.reviews.list_(db.session, actor)
    found = mr.reviews.list_(db.session, actor, **filters) if applied else every
    return render_template(
        'revisiones_direccion/list.html',
        revisiones=found,
        acciones=mr.action_counts(db.session, actor, (r.id for r in found), today=local_today()),
        anios=sorted({r.fecha.year for r in every}, reverse=True),
        estados=list(EstadoRevisionDireccion),
        filtros=filters,
        applied=applied,
    )


def _review_context(actor: Actor, review_id: int) -> dict[str, Any]:
    """What a review's page and its minutes show, judged on ``local_today``.

    The review, its actions (soonest deadline first) with the overdue ones, the
    inputs as ``input_sections`` with their cut-off date, the names of the
    person who held it and of the actions' owners, and the changes to the QMS
    each ``cambio_sgc`` action gives rise to (``None`` without their read grant).
    """
    today = local_today()
    revision = mr.reviews.get(db.session, actor, review_id)
    acciones = mr.actions.list_(db.session, actor, revision_id=review_id)
    datos = mr.preview(db.session, actor, revision, today=today)
    return dict(
        revision=revision,
        acciones=acciones,
        vencidas={a.id for a in acciones if mr.is_overdue(a, today=today)},
        secciones=input_sections(datos),
        corte=_day(_at(datos, 'fecha_corte')),
        personas=people.names(db.session, actor, (
            revision.celebrada_por_id, *(a.responsable_id for a in acciones))),
        cambios={a.id: citing_changes(actor, 'revision_direccion', a.id) for a in acciones
                 if a.tipo is TipoAccionRevision.cambio_sgc},
    )


@bp.route('/<int:review_id>', methods=['GET'])
@login_required
@require_permission('read', 'management_review')
def show_review(review_id: int) -> ResponseReturnValue:
    """A review's page: the meeting, the inputs with their comments, conclusions, actions."""
    return render_template('revisiones_direccion/detail.html',
                           **_review_context(current_actor(), review_id),
                           puede_decidir=may_decide(),
                           puede_planificar=can('create', 'qms_changes'))


@bp.route('/<int:review_id>/pdf', methods=['GET'])
@login_required
@require_permission('read', 'management_review')
def export_pdf(review_id: int) -> ResponseReturnValue:
    """The review's minutes as a PDF: the meeting, inputs, conclusions and actions."""
    context = _review_context(current_actor(), review_id)
    try:
        pdf = render_pdf('revisiones_direccion/pdf_template.html', **context)
    except Exception:
        current_app.logger.exception('Error generating PDF for management review %s', review_id)
        flash('Error al generar el acta de la revisión por la dirección.', 'danger')
        return _review_page(review_id)
    return pdf_response(pdf, f"acta_revision_direccion_{context['revision'].fecha:%Y-%m-%d}.pdf")


def _review_form(actor: Actor, revision: RevisionDireccion | None = None
                 ) -> RevisionDireccionForm:
    """The draft form; the attendee picker offers active people and the current attendees."""
    form = RevisionDireccionForm(obj=revision)
    offered = people.choices(db.session, actor) + [
        person for person in getattr(revision, 'asistentes', []) if not person.activo]
    form.asistente_ids.choices = person_choices(offered)[1:]  # no empty choice
    return form


@bp.route('/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'management_review')
def new_review() -> ResponseReturnValue:
    """Create a draft review; the form starts on today's date."""
    actor = current_actor()
    form = _review_form(actor)
    if request.method == 'GET':
        form.fecha.data = local_today()
    if form.validate_on_submit() and (created := saved(lambda: mr.reviews.create(
            db.session, actor, form_data(form, REVIEW_FIELDS)))):
        flash(f'Revisión por la dirección del {created.fecha:%d/%m/%Y} creada.', 'success')
        return _review_page(created.id)
    return render_template('revisiones_direccion/form.html', form=form, revision=None,
                           titulo='Nueva revisión por la dirección')


@bp.route('/<int:review_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'management_review')
def edit_review(review_id: int) -> ResponseReturnValue:
    """Edit a draft: its meeting, period, attendees and comments."""
    actor = current_actor()
    revision = mr.reviews.get(db.session, actor, review_id)
    if revision.estado is not BORRADOR:
        return _refused(review_id, mr.ONLY_COMMENTS if revision.estado is CELEBRADA
                        else mr.REVIEW_CLOSED)
    form = _review_form(actor, revision)
    if form.validate_on_submit() and committed(lambda: mr.reviews.update(
            db.session, actor, review_id, form_data(form, REVIEW_FIELDS))):
        flash('Revisión por la dirección actualizada.', 'success')
        return _review_page(review_id)
    return render_template('revisiones_direccion/form.html', form=form, revision=revision,
                           titulo='Editar la revisión por la dirección')


@bp.route('/<int:review_id>/conclusiones', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'management_review')
def edit_conclusions(review_id: int) -> ResponseReturnValue:
    """Write the comments and conclusions of a held review."""
    actor = current_actor()
    revision = mr.reviews.get(db.session, actor, review_id)
    if revision.estado is not CELEBRADA:
        return _refused(review_id, mr.REVIEW_CLOSED if revision.estado is CERRADA
                        else CONCLUSIONS_WHEN_HELD)
    form = ConclusionesRevisionForm(obj=revision)
    if form.validate_on_submit() and committed(lambda: mr.reviews.update(
            db.session, actor, review_id, form_data(form, HELD_FIELDS))):
        flash('Comentarios y conclusiones guardados.', 'success')
        return _review_page(review_id)
    return render_template('revisiones_direccion/form.html', form=form, revision=revision,
                           titulo='Comentarios y conclusiones')


@bp.route('/<int:review_id>/celebrar', methods=['POST'])
@login_required
@require_permission('update', 'management_review')
def hold_review(review_id: int) -> ResponseReturnValue:
    """Hold a draft today, freezing its inputs; administrators only (MR4)."""
    if not may_decide():
        raise PermissionDenied()
    if committed(lambda: mr.hold(db.session, current_actor(), review_id, today=local_today())):
        flash('Revisión celebrada: sus datos de entrada han quedado congelados.', 'success')
    return _review_page(review_id)


@bp.route('/<int:review_id>/cerrar', methods=['POST'])
@login_required
@require_permission('update', 'management_review')
def close_review(review_id: int) -> ResponseReturnValue:
    """Close a held review with its conclusions today; administrators only (MR4)."""
    if not may_decide():
        raise PermissionDenied()
    if committed(lambda: mr.close(db.session, current_actor(), review_id, today=local_today())):
        flash('Revisión cerrada.', 'success')
    return _review_page(review_id)


@bp.route('/<int:review_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'management_review')
def delete_review(review_id: int) -> ResponseReturnValue:
    """Delete a draft without actions (administrators, MR6)."""
    if committed(lambda: mr.reviews.delete(db.session, current_actor(), review_id)):
        flash('Revisión por la dirección eliminada.', 'success')
        return redirect(url_for('revision_direccion.list_reviews'))
    return _review_page(review_id)


# -- actions ---------------------------------------------------------------------------


def _action(actor: Actor, review_id: int, action_id: int) -> AccionRevision:
    """An action of the review; one of another review is a 404."""
    found = mr.actions.get(db.session, actor, action_id)
    if found.revision_id != review_id:
        raise NotFound(mr.ACTION_NOT_FOUND)
    return found


def _action_form(actor: Actor, accion: AccionRevision | None = None, *,
                 follow_up_only: bool = False) -> AccionRevisionForm:
    """The action form; ``follow_up_only`` keeps the state, completion date and result."""
    form = AccionRevisionForm(obj=accion)
    if follow_up_only:
        for name in ACTION_FIELDS:
            if name not in FOLLOW_UP:
                delattr(form, name)
    else:
        form.responsable_id.choices = person_choices(people.choices(
            db.session, actor, include=getattr(accion, 'responsable_id', None)),
            empty=CHOOSE_PERSON)
    return form


@bp.route('/<int:review_id>/acciones/nueva', methods=['GET', 'POST'])
@login_required
@require_permission('create', 'management_review')
def new_action(review_id: int) -> ResponseReturnValue:
    """Add an action to a review that is not closed."""
    actor = current_actor()
    revision = mr.reviews.get(db.session, actor, review_id)
    if revision.estado is CERRADA:
        return _refused(review_id, mr.REVIEW_CLOSED_FOR_ACTIONS)
    form = _action_form(actor)
    if form.validate_on_submit() and saved(lambda: mr.actions.create(
            db.session, actor, form_data(form, ACTION_FIELDS) | {'revision_id': review_id},
            today=local_today())):
        flash('Acción añadida a la revisión.', 'success')
        return _review_page(review_id)
    return render_template('revisiones_direccion/action_form.html', form=form,
                           revision=revision, accion=None)


@bp.route('/<int:review_id>/acciones/<int:action_id>/editar', methods=['GET', 'POST'])
@login_required
@require_permission('update', 'management_review')
def edit_action(review_id: int, action_id: int) -> ResponseReturnValue:
    """Edit an action; once its review is closed, only its follow-up (MR3)."""
    actor = current_actor()
    accion = _action(actor, review_id, action_id)
    revision = mr.reviews.get(db.session, actor, review_id)
    closed = revision.estado is CERRADA
    form = _action_form(actor, accion, follow_up_only=closed)
    if form.validate_on_submit() and committed(lambda: mr.actions.update(
            db.session, actor, action_id, form_data(form, FOLLOW_UP if closed else ACTION_FIELDS),
            today=local_today())):
        flash('Acción actualizada.', 'success')
        return _review_page(review_id)
    return render_template('revisiones_direccion/action_form.html', form=form,
                           revision=revision, accion=accion, cerrada=closed,
                           personas=people.names(db.session, actor, (accion.responsable_id,)))


@bp.route('/<int:review_id>/acciones/<int:action_id>/eliminar', methods=['POST'])
@login_required
@require_permission('delete', 'management_review')
def delete_action(review_id: int, action_id: int) -> ResponseReturnValue:
    """Delete an action of a review that is not closed (administrators)."""
    actor = current_actor()
    _action(actor, review_id, action_id)
    if committed(lambda: mr.actions.delete(db.session, actor, action_id)):
        flash('Acción eliminada.', 'success')
    return _review_page(review_id)


# -- action register -------------------------------------------------------------------

ACTION_STATES = frozenset(member.name for member in EstadoAccionRevision)
ACTION_TYPES = frozenset(member.name for member in TipoAccionRevision)
OVERDUE_FLAGS = {'1': True, '0': False}


@bp.route('/acciones', methods=['GET'])
@login_required
@require_permission('read', 'management_review')
def list_actions() -> ResponseReturnValue:
    """Every review's actions matching the state, owner, type and overdue filters."""
    actor = current_actor()
    today = local_today()
    every = mr.actions.list_(db.session, actor)
    owners = people.names(db.session, actor, (a.responsable_id for a in every))
    estado, tipo = request.args.get('estado', ''), request.args.get('tipo', '')
    owner = request.args.get('responsable_id', type=int)
    filters = {'estado': estado if estado in ACTION_STATES else None,
               'responsable_id': owner if owner in owners else None,
               'tipo': tipo if tipo in ACTION_TYPES else None,
               'vencida': OVERDUE_FLAGS.get(request.args.get('vencida', ''))}
    applied = sum(value is not None for value in filters.values())
    found = mr.actions.list_(db.session, actor, today=today, **filters) if applied else every
    return render_template(
        'revisiones_direccion/actions.html',
        acciones=found,
        vencidas={a.id for a in found if mr.is_overdue(a, today=today)},
        fechas={r.id: r.fecha for r in mr.reviews.list_(db.session, actor)},
        personas=owners,
        responsables=sorted(owners.items(), key=lambda item: item[1].casefold()),
        estados=list(EstadoAccionRevision),
        tipos=list(TipoAccionRevision),
        filtros=filters,
        applied=applied,
        puede_planificar=can('create', 'qms_changes'),
    )
