"""The QMS modules an agent can reach: slug, policy resource, service and fields.

One generic tool surface serves every module, so this table is what tells an
agent (through ``qms_modules``) which fields, allowed values and filters each
module has. Tests keep the declared fields in step with the services.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy.orm import object_session

from ..audit_notifications import local_today
from ..models import (
    CompetenceEvaluation, CompetenceType, DecisionOportunidad, DocumentCategory, EficaciaAccion,
    EstadoAccionCorrectiva, EstadoAccionRevision, EstadoAccionRiesgo, EstadoAuditoriaEnum,
    EstadoNoConformidad, EstadoObjetivo, EstadoOportunidad, EstadoProgramaAuditoria,
    EstadoRevisionDireccion, EstadoRiesgo, FrecuenciaMedicion, GravedadNoConformidad,
    NivelRiesgo, OrigenNoConformidad, OrigenRiesgo, PrioridadOportunidad, SentidoObjetivo,
    TipoAccionRevision, TipoHallazgo, TratamientoRiesgo,
)
from ..services import (
    audit_findings, audit_indicators, audit_programmes, audits, competence, corrective_actions,
    document_revisions, documents, improvements, management_reviews,
    nonconformities, people, process_operations, quality_objectives, risks,
    roles_responsibilities, satisfaction, stakeholders, training, training_resources,
)
from ..services.policy import Resource


@dataclass(frozen=True)
class FieldDef:
    """One writable field or list filter, as described to an agent.

    ``type`` is ``string``, ``date`` (YYYY-MM-DD), ``datetime`` (ISO 8601),
    ``integer``, ``integer_list`` (a JSON array of integers), ``number`` (a
    JSON number or decimal text; records return it as decimal text with four
    decimals, e.g. ``"95.0000"``), ``boolean`` or ``enum`` (one of ``allowed``).
    ``members`` names the Python enum a filter must be turned into before the
    service sees it; without it an enum filter is passed on as the plain string.
    ``note`` is a short hint shown to the agent when the type and flags do not
    say enough.
    """

    name: str
    type: str = "string"
    required: bool = False
    allowed: tuple[str, ...] = ()
    members: type[Enum] | None = None
    note: str = ""


@dataclass(frozen=True)
class Module:
    """One register as the tools expose it.

    ``extras`` adds values that are not columns of the record (for example the
    ids of a many-to-many relation) to what the tools return. ``service`` is a
    service module, or an object with the same functions (``competence``).
    """

    slug: str
    label: str
    resource: Resource
    service: Any
    fields: tuple[FieldDef, ...]
    filters: tuple[FieldDef, ...] = ()
    extras: Callable[[Any], dict[str, Any]] | None = None


def _person_extras(person: Any) -> dict[str, Any]:
    return {"rol_ids": person.rol_ids}


def _audit_extras(found: Any) -> dict[str, Any]:
    return {"equipo_ids": found.equipo_ids}


def _action_extras(action: Any) -> dict[str, Any]:
    return {"estado": action.estado}  # derived, never stored


def _document_extras(document: Any) -> dict[str, Any]:
    """The revision in force, which every role reads; drafts never leave the web."""
    revision = document_revisions.in_force(object_session(document), document.id)
    return {"effective_revision": None if revision is None else {
        "numero": revision.numero, "content": revision.content,
        "effective_from": revision.effective_from, "legacy_version": revision.legacy_version,
    }}


def _objective_extras(objective: Any) -> dict[str, Any]:
    """The objective's latest value, target met, trend and next measurement (QO2, QO3)."""
    session = object_session(objective)
    return quality_objectives.summary(
        objective, quality_objectives.measurements_of(session, objective.id), today=local_today())


def _measurement_extras(measurement: Any) -> dict[str, Any]:
    return {"cumple": quality_objectives.measurement_meets(object_session(measurement),
                                                           measurement)}


def _review_extras(review: Any) -> dict[str, Any]:
    """The attendees and the inputs of 9.3.2: frozen once held, live in a draft (MR2)."""
    return {"asistente_ids": review.asistente_ids,
            "preview": management_reviews.preview_of(object_session(review), review,
                                                      today=local_today())}


def _review_action_extras(action: Any) -> dict[str, Any]:
    return {"vencida": management_reviews.is_overdue(action, today=local_today())}


def _risk_extras(risk: Any) -> dict[str, Any]:
    """The levels before and after the actions and whether the review is overdue."""
    return {"nivel": risk.nivel, "nivel_residual": risk.nivel_residual,
            "revision_vencida": risks.revision_vencida(risk, today=local_today())}


def _opportunity_extras(opportunity: Any) -> dict[str, Any]:
    return {"prioridad": opportunity.prioridad,
            "revision_vencida": risks.revision_vencida(opportunity, today=local_today())}


def _risk_action_extras(action: Any) -> dict[str, Any]:
    return {"vencida": risks.is_overdue(action, today=local_today())}


@dataclass(frozen=True)
class _DatedWrites:
    """A register whose writes take the adapter's date (``local_today``), used like a service."""

    register: Any

    @property
    def SPEC(self) -> Any:  # noqa: N802 - named like the services' constant
        return self.register.SPEC

    def get(self, session: Any, actor: Any, record_id: int) -> Any:
        return self.register.get(session, actor, record_id)

    def list_(self, session: Any, actor: Any, **filters: Any) -> list[Any]:
        return self.register.list_(session, actor, **filters)

    def list_page(self, session: Any, actor: Any, **kwargs: Any) -> tuple[list[Any], int]:
        return self.register.list_page(session, actor, **kwargs)

    def create(self, session: Any, actor: Any, data: Any) -> Any:
        return self.register.create(session, actor, data, today=local_today())

    def update(self, session: Any, actor: Any, record_id: int, data: Any) -> Any:
        return self.register.update(session, actor, record_id, data, today=local_today())


class _DatedRegister(_DatedWrites):
    """Also passes the adapter's date to the lists, whose ``vencida`` filter needs it."""

    def list_(self, session: Any, actor: Any, **filters: Any) -> list[Any]:
        return self.register.list_(session, actor, **filters, today=local_today())

    def list_page(self, session: Any, actor: Any, **kwargs: Any) -> tuple[list[Any], int]:
        return self.register.list_page(session, actor, **kwargs, today=local_today())


def _f(name: str, type: str = "string", required: bool = False, allowed=(), members=None,
       note: str = "") -> FieldDef:
    if members is not None:
        allowed = members.__members__
    return FieldDef(name, type, required, tuple(allowed), members, note)


def _legacy_name(name: str, link: str) -> FieldDef:
    """A legacy free-text name that the person cited by ``link`` can fill."""
    return _f(name, note=(
        f"Legacy free-text name, never matched to a person. Required on create unless "
        f"«{link}» cites a person (personas); left blank, it takes that person's name."
    ))


def _register_fields(estado: type, assessment: tuple[FieldDef, ...]) -> tuple[FieldDef, ...]:
    """Fields a risk and an opportunity share, around their assessment."""
    return (
        _f("titulo", required=True), _f("descripcion", required=True),
        _f("origen", "enum", True, members=OrigenRiesgo),
        _f("parte_interesada_id", "integer", note="Interested party (partes_interesadas)."),
        _f("proceso_id", "integer", note="Process (procesos) it belongs to."),
        _f("responsable_id", "integer", True, note=(
            "Person (personas) who owns it; records migrated from the old register may "
            "have none.")),
        *assessment,
        _f("estado", "enum", members=estado, note=f"Starts {next(iter(estado)).name}."),
        _f("fecha_identificacion", "date", note="Defaults to today; never in the future."),
        _f("fecha_revision", "date", note="Next review; not before fecha_identificacion."),
        _f("notas"),
    )


def _register_filters(band: FieldDef, estado: type) -> tuple[FieldDef, ...]:
    return (
        _f("estado", "enum", members=estado), band,
        _f("responsable_id", "integer"), _f("proceso_id", "integer"),
        _f("origen", "enum", members=OrigenRiesgo),
        _f("sin_valorar", "boolean", note="Not assessed yet."),
        _f("revision_vencida", "boolean", note="Open, with fecha_revision before today."),
    )


def _scale(name: str, note: str = "") -> FieldDef:
    return _f(name, "integer", note=note or "1 to 5; give both values of the pair or neither.")


def _risk_modules() -> tuple[Module, ...]:
    """Risks, opportunities and their actions (6.1), sharing ``RISKS_OPPORTUNITIES`` (RO6).

    Enums show the Spanish label; the filters take the member name. Risk records add
    ``nivel`` and ``nivel_residual`` (bajo 1-4, medio 5-9, alto 10-16, critico 20-25 from
    the product of the pair), opportunity records ``prioridad`` (baja 1-6, media 8-12,
    alta 15-25); both add ``revision_vencida``, action records ``vencida``.
    """
    residual = "Residual value after the actions, 1 to 5; needs probabilidad and impacto."
    return (
        Module("riesgos", "Risks", Resource.RISKS_OPPORTUNITIES,
               _DatedRegister(risks.risks), (
            *_register_fields(EstadoRiesgo, (
                _scale("probabilidad"), _scale("impacto"),
                _f("tratamiento", "enum", members=TratamientoRiesgo),
                _scale("probabilidad_residual", residual), _scale("impacto_residual", residual),
            )),
        ), _register_filters(_f("nivel", "enum", members=NivelRiesgo), EstadoRiesgo),
            extras=_risk_extras),
        Module("oportunidades", "Opportunities", Resource.RISKS_OPPORTUNITIES,
               _DatedRegister(risks.opportunities), (
            *_register_fields(EstadoOportunidad, (
                _scale("beneficio"), _scale("viabilidad"),
                _f("decision", "enum", members=DecisionOportunidad),
            )),
        ), _register_filters(_f("prioridad", "enum", members=PrioridadOportunidad),
                             EstadoOportunidad),
            extras=_opportunity_extras),
        Module("acciones_riesgo", "Actions on risks and opportunities",
               Resource.RISKS_OPPORTUNITIES, _DatedRegister(risks.actions), (
            _f("riesgo_id", "integer", note=(
                "Risk (riesgos) the action addresses; give it or oportunidad_id, not both. "
                "It cannot change later, and a cerrado risk takes no new actions.")),
            _f("oportunidad_id", "integer", note=(
                "Opportunity (oportunidades); an aprovechada or descartada one takes no new "
                "actions.")),
            _f("descripcion", required=True),
            _f("responsable_id", "integer", True, note="Person (personas) who owns the action."),
            _f("fecha_limite", "date", True),
            _f("estado", "enum", members=EstadoAccionRiesgo, note="Starts pendiente."),
            _f("fecha_completada", "date", note=(
                "Given exactly when estado is completada; not in the future.")),
            _f("eficacia", "enum", members=EficaciaAccion, note=(
                "Effectiveness, with fecha_evaluacion, once completada.")),
            _f("fecha_evaluacion", "date", note=(
                "Not before fecha_completada nor in the future.")),
            _f("comentario_eficacia"),
        ), (_f("riesgo_id", "integer"), _f("oportunidad_id", "integer"),
            _f("estado", "enum", members=EstadoAccionRiesgo), _f("responsable_id", "integer"),
            _f("vencida", "boolean", note="Pendiente or en_curso past fecha_limite.")),
            extras=_risk_action_extras),
    )


# ``responsable_id``, ``auditor_id`` and ``persona_id`` are ids of ``personas``
# records (an existing person, active when newly chosen; ``null`` clears the
# link). The free-text ``responsable``, ``auditor`` and ``personal`` stay as
# legacy names and are never matched to a person. ``auditor`` and ``personal``
# are not flagged required: the services require them only when no person is
# cited (``people.fill_name``), and their note says so.
_MODULES = (
    # ``estado`` is not writable: a record starts "abierta" and only the service's
    # explicit transitions move it (records show the Spanish label, filters take
    # the member name, as for ``origen`` and ``gravedad``). A closed or cancelled
    # record refuses updates; closing is web-only.
    Module("no_conformidades", "Non-conformities", Resource.NONCONFORMITIES, nonconformities, (
        _f("descripcion", required=True), _f("fecha_detectada", "date", True),
        _f("origen", "enum", members=OrigenNoConformidad),
        _f("gravedad", "enum", members=GravedadNoConformidad),
        _legacy_name("responsable", "responsable_id"), _f("responsable_id", "integer"),
        _f("contencion"), _f("causa_raiz"),
        _f("accion_correctiva", note="Legacy free-text corrective action."),
    ), (_f("descripcion"), _f("estado", "enum", members=EstadoNoConformidad),
        _f("fecha_detectada", "date"), _f("origen", "enum", members=OrigenNoConformidad),
        _f("gravedad", "enum", members=GravedadNoConformidad))),
    # ``auditor_id`` is the lead auditor. No auditor (lead or team) may be the
    # audited-area owner, and the date must fall in the programme's year.
    Module("auditorias", "Audits", Resource.AUDITS, audits, (
        _f("area_auditada", required=True), _f("fecha", "date", True),
        _legacy_name("auditor", "auditor_id"), _f("auditor_id", "integer"),
        _f("resultado", required=True), _f("accion_correctiva"),
        _f("estado", "enum", members=EstadoAuditoriaEnum),
        _f("programa_id", "integer", note=(
            "Audit programme (programas_auditoria); the audit date must fall in its year.")),
        _f("alcance"), _f("criterios", note="E.g. «ISO 9001:2026, procedimientos internos»."),
        _f("responsable_area_id", "integer", note=(
            "Person (personas) who owns the audited area; never the lead auditor or a "
            "team member.")),
        _f("equipo_ids", "integer_list", note="People (personas) in the audit team; replaces it."),
    ), (_f("area"), _f("auditor"), _f("estado", "enum", members=EstadoAuditoriaEnum),
        _f("fecha_inicio", "date"), _f("fecha_fin", "date"), _f("programa_id", "integer")),
        extras=_audit_extras),
    # Shares the ``AUDITS`` grant (AP7). Records show ``estado`` (Spanish label;
    # the filter takes the member name) and the approval columns, which no tool
    # writes: approving is web-only, for administrators. An approved programme's
    # year never changes.
    Module("programas_auditoria", "Audit programmes", Resource.AUDITS, audit_programmes, (
        _f("anio", "integer", True, note="Year of the programme (2000-2100), one per year."),
        _f("objetivos", required=True), _f("alcance", required=True),
    ), (_f("anio", "integer"), _f("estado", "enum", members=EstadoProgramaAuditoria))),
    # Shares the ``AUDITS`` grant (AP7). A non-conformity finding (``nc_mayor``,
    # ``nc_menor``) creates its non-conformity in the same write (AP5); records show
    # its id as ``no_conformidad_id``, which no tool writes. ``tipo`` shows the
    # Spanish label; the filter takes the member name.
    Module("hallazgos_auditoria", "Audit findings", Resource.AUDITS, audit_findings, (
        _f("auditoria_id", "integer", True, note=(
            "Audit (auditorias) the finding belongs to; it cannot change later and a "
            "cancelled audit takes no findings.")),
        _f("tipo", "enum", True, members=TipoHallazgo, note=(
            "nc_mayor and nc_menor create a non-conformity (no_conformidades) with that "
            "severity, returned as no_conformidad_id; changing the type never deletes it.")),
        _f("descripcion", required=True),
        _f("clausula", note="ISO 9001 clause: dotted numbers from 4 to 10, e.g. «8.5.1»."),
        _f("evidencia"),
    ), (_f("auditoria_id", "integer"), _f("tipo", "enum", members=TipoHallazgo))),
    # Read-only here (DC-1 of ``document-control``): every role reads the documents
    # it may see with their revision in force, and the policy refuses ``qms_create``
    # and ``qms_update`` on the mcp channel, so no field is writable. The filters
    # run in ``documents.list_page`` (DC-4).
    Module("documentos", "Documents", Resource.DOCUMENTS, documents, (), (
        _f("category", "enum", members=DocumentCategory),
        _f("owner_id", "integer", note="Person (personas) who owns the document."),
        _f("status", "enum", allowed=documents.STATUSES, note=(
            "vigente: in force; sin_publicar: in use with no revision in force; "
            "de_baja: withdrawn.")),
    ), extras=_document_extras),
    Module("capacitaciones", "Training", Resource.TRAINING, training, (
        _f("tema", required=True), _f("fecha", "date", True),
        _legacy_name("personal", "persona_id"),
        _f("duracion_horas", "integer"), _f("evaluacion_final"), _f("persona_id", "integer"),
    ), (_f("tema"), _f("fecha", "date"), _f("personal"))),
    Module("satisfaccion_clientes", "Customer satisfaction", Resource.CUSTOMER_SATISFACTION,
           satisfaction, (
        _f("cliente", required=True), _f("fecha_encuesta", "date", True),
        _f("puntuacion", "integer", True), _f("comentarios"),
    ), (_f("cliente"), _f("puntuacion_minima", "integer"))),
    Module("partes_interesadas", "Interested parties", Resource.INTERESTED_PARTIES,
           stakeholders, (
        _f("nombre", required=True), _f("necesidades_expectativas"),
        _f("requisitos_identificados"), _f("objetivo_estrategico"),
    )),
    Module("mejoras", "Improvements", Resource.IMPROVEMENTS, improvements, (
        _f("no_conformidad", required=True), _f("accion_correctiva"), _f("accion_preventiva"),
    )),
    Module("roles_responsabilidades", "Roles and responsibilities",
           Resource.ROLES_RESPONSIBILITIES, roles_responsibilities, (
        _f("rol", required=True), _f("compromiso_calidad", "boolean"),
        _f("descripcion_politica_calidad"),
    )),
    Module("recursos_capacitacion", "Training resources", Resource.TRAINING_RESOURCES,
           training_resources, (
        _f("recurso_necesario", required=True), _f("capacitacion_personal", "boolean"),
        _f("descripcion_documentacion"),
    )),
    Module("procesos", "Process operations", Resource.PROCESS_OPERATIONS,
           process_operations, (
        _f("proceso", required=True), _f("criterio_calidad"),
        _f("control_proveedor", "boolean"), _f("no_conformidad"),
    )),
    Module("indicadores_auditoria", "Audit indicators", Resource.AUDIT_INDICATORS,
           audit_indicators, (
        _f("area_auditoria", required=True), _f("fecha_auditoria", "datetime"),
        _f("resultado"), _f("accion_correctiva"), _f("indicador_desempeno"),
    )),
    # ``rol_ids`` replaces the roles held; ``user_id`` links at most one user account.
    Module("personas", "People", Resource.PEOPLE, people, (
        _f("nombre", required=True), _f("email"), _f("user_id", "integer"),
        _f("activo", "boolean"), _f("notas"), _f("rol_ids", "integer_list"),
    ), (_f("nombre"), _f("rol_id", "integer"), _f("activo", "boolean")),
        extras=_person_extras),
    # Both competence registers share the ``COMPETENCE`` resource. ``rol_id`` names a
    # role, ``persona_id`` and ``evaluador_id`` people, ``requisito_id`` a required
    # competence and ``capacitacion_id`` a training. An evaluation other than
    # ``pendiente`` needs ``fecha_evaluacion`` and ``evaluador_id``.
    Module("competencias_requeridas", "Competence requirements", Resource.COMPETENCE,
           competence.requirements, (
        _f("rol_id", "integer", True), _f("tipo", "enum", True, members=CompetenceType),
        _f("descripcion", required=True), _f("criterio"),
    ), (_f("rol_id", "integer"), _f("tipo", "enum", members=CompetenceType))),
    Module("competencias_acreditadas", "Competence records", Resource.COMPETENCE,
           competence.records, (
        _f("persona_id", "integer", True), _f("requisito_id", "integer"),
        _f("evidencia", required=True), _f("capacitacion_id", "integer"),
        _f("fecha_obtencion", "date", True), _f("fecha_caducidad", "date"),
        _f("evaluacion_eficacia", "enum", members=CompetenceEvaluation),
        _f("fecha_evaluacion", "date"), _f("evaluador_id", "integer"),
    ), (_f("persona_id", "integer"), _f("requisito_id", "integer"),
        _f("evaluacion_eficacia", "enum", members=CompetenceEvaluation))),
    # Records add the derived ``estado`` (Spanish label; the filter takes the member
    # name) and show the verification columns, which no tool writes: verifying an
    # action's effectiveness is a separate service step (administrators and
    # auditors). Each write moves the nonconformity's state along (decision N5).
    Module("acciones_correctivas", "Corrective actions", Resource.CORRECTIVE_ACTIONS,
           corrective_actions, (
        _f("no_conformidad_id", "integer", True, note=(
            "Id of an open non-conformity (no_conformidades); it cannot change later.")),
        _f("descripcion", required=True),
        _f("responsable_id", "integer", True, note="Person (personas) who owns the action."),
        _f("fecha_prevista", "date", True),
        _f("fecha_realizada", "date", note=(
            "Set when the action is done; not before the non-conformity was detected. "
            "Verified actions are read-only.")),
    ), (_f("no_conformidad_id", "integer"), _f("responsable_id", "integer"),
        _f("estado", "enum", members=EstadoAccionCorrectiva)),
        extras=_action_extras),
    # Both share ``QUALITY_OBJECTIVES`` (QO4). Enums show the Spanish label (``sentido``
    # shows «≥» or «≤»); the filters take the member name. Numbers come back as decimal
    # text. Objective records add ``ultimo_valor``, ``ultima_fecha``, ``cumple``,
    # ``tendencia`` (sube, baja, estable), ``medicion_vencida`` and ``proxima_medicion``;
    # measurement records add ``cumple``.
    Module("objetivos_calidad", "Quality objectives", Resource.QUALITY_OBJECTIVES,
           quality_objectives.objectives, (
        _f("titulo", required=True), _f("descripcion"),
        _f("proceso_id", "integer", note="Process (procesos) the objective belongs to."),
        _f("indicador", required=True, note="What is measured, e.g. «% de pedidos a tiempo»."),
        _f("unidad", note="Unit of the indicator, e.g. «%»."),
        _f("meta", "number", True, note=(
            "Target value. With sentido, fixed once the objective has measurements.")),
        _f("sentido", "enum", True, members=SentidoObjetivo, note=(
            "mayor_igual: met when the value is at or above meta; menor_igual: at or below.")),
        _f("valor_inicial", "number", note="Baseline value."),
        _f("responsable_id", "integer", True, note="Person (personas) who owns the objective."),
        _f("anio", "integer", note="Year (2000-2100); defaults to the year of fecha_inicio."),
        _f("fecha_inicio", "date", True),
        _f("fecha_limite", "date", True, note="Deadline; not before fecha_inicio."),
        _f("frecuencia", "enum", True, members=FrecuenciaMedicion,
           note="How often it is measured; a measurement is overdue one period after the last."),
        _f("recursos", note="Resources needed."),
        _f("evaluacion", note="How the results are evaluated."),
        _f("estado", "enum", members=EstadoObjetivo,
           note="Starts activo; measurements are recorded only while activo."),
    ), (_f("anio", "integer"), _f("estado", "enum", members=EstadoObjetivo),
        _f("responsable_id", "integer"), _f("proceso_id", "integer")),
        extras=_objective_extras),
    Module("mediciones_objetivo", "Quality objective measurements",
           Resource.QUALITY_OBJECTIVES, _DatedWrites(quality_objectives.measurements), (
        _f("objetivo_id", "integer", True, note=(
            "Objective (objetivos_calidad); it must be activo and cannot change later.")),
        _f("fecha", "date", True, note=(
            "Inside the objective's period and not in the future; one per objective and date.")),
        _f("valor", "number", True), _f("comentario"),
        _f("registrado_por_id", "integer", note="Person (personas) who recorded it."),
    ), (_f("objetivo_id", "integer"),), extras=_measurement_extras),
    # Both share ``MANAGEMENT_REVIEW`` (MR5): administrators and auditors only. ``estado``
    # shows the Spanish label and no tool writes it: holding, which freezes the inputs
    # into ``datos_entrada`` and records ``celebrada_por_id``, and closing, which records
    # ``fecha_cierre``, are web-only, for administrators (MR4). A held review takes only
    # comments and conclusions, a closed one nothing. Review records add ``asistente_ids``
    # and ``preview`` (the 9.3.2 inputs of the period: frozen once held, live in a draft);
    # action records add ``vencida``.
    Module("revisiones_direccion", "Management reviews", Resource.MANAGEMENT_REVIEW,
           management_reviews.reviews, (
        _f("fecha", "date", True, note="Meeting date."),
        _f("periodo_desde", "date", True, note="First day of the period reviewed."),
        _f("periodo_hasta", "date", True, note=(
            "Last day of the period reviewed; not before periodo_desde nor after fecha.")),
        _f("asistente_ids", "integer_list", note=(
            "People (personas) attending; replaces them. Holding needs at least one.")),
        _f("comentario_acciones_previas", note="Status of actions from previous reviews."),
        _f("comentario_contexto", note=(
            "Changes in external and internal issues, interested parties, risks and "
            "opportunities.")),
        _f("comentario_desempeno", note=(
            "Performance and effectiveness of the QMS, including external providers.")),
        _f("comentario_recursos", note="Adequacy of resources."),
        _f("comentario_riesgos", note=(
            "Effectiveness of the actions taken on risks and opportunities.")),
        _f("comentario_mejora", note="Opportunities for improvement."),
        _f("conclusiones", note=(
            "Suitability, adequacy and effectiveness of the QMS; needed to close.")),
    ), (_f("estado", "enum", members=EstadoRevisionDireccion),
        _f("anio", "integer", note="Year of the meeting date.")),
        extras=_review_extras),
    Module("acciones_revision", "Management review actions", Resource.MANAGEMENT_REVIEW,
           _DatedRegister(management_reviews.actions), (
        _f("revision_id", "integer", True, note=(
            "Review (revisiones_direccion), not closed; it cannot change later. Once the "
            "review is closed only estado, fecha_completada and resultado change.")),
        _f("tipo", "enum", True, members=TipoAccionRevision),
        _f("descripcion", required=True),
        _f("responsable_id", "integer", True, note="Person (personas) who owns the action."),
        _f("fecha_limite", "date", True),
        _f("estado", "enum", members=EstadoAccionRevision, note="Starts pendiente."),
        _f("fecha_completada", "date", note=(
            "Given exactly when estado is completada; not in the future nor before the "
            "review's date.")),
        _f("resultado"),
    ), (_f("revision_id", "integer"), _f("estado", "enum", members=EstadoAccionRevision),
        _f("responsable_id", "integer"), _f("tipo", "enum", members=TipoAccionRevision),
        _f("vencida", "boolean", note="Pendiente or en_curso past fecha_limite.")),
        extras=_review_action_extras),
    *_risk_modules(),
)

MODULES: dict[str, Module] = {module.slug: module for module in _MODULES}
