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
    CATEGORIAS_POR_TIPO, CambioSGC, CanalContacto, CategoriaCuestion, CompetenceEvaluation, CompetenceType,
    EstadoReclamacion, TipoConfirmacion, TipoEncuesta,
    DecisionOportunidad, DocumentCategory, EfectoCuestion, EficaciaAccion, EstadoAccionCorrectiva,
    EstadoAccionRevision, EstadoAccionRiesgo, EstadoAlcance, EstadoAuditoriaEnum, EstadoCambio,
    EstadoCuestion, EstadoMejora, OrigenMejora, EstadoNoConformidad, EstadoObjetivo, EstadoOportunidad,
    EstadoProgramaAuditoria, EstadoProveedor, EstadoRevisionDireccion, EstadoRiesgo,
    EstadoTareaCambio, FrecuenciaMedicion, GravedadNoConformidad, NivelRiesgo, OrigenCambio,
    OrigenNoConformidad, OrigenRiesgo, PrioridadOportunidad, ResultadoEvaluacion, SentidoObjetivo,
    TipoAccionRevision, TipoContexto, TipoHallazgo, TipoProceso, TipoSuministro, TratamientoRiesgo,
    TratamientoSalidaNoConforme,
)
from ..services import (
    audit_findings, audit_indicators, audit_programmes, audits, clientes, competence, context_issues,
    reclamaciones, read_confirmations,
    corrective_actions, document_revisions, documents, improvements, management_reviews,
    nonconformities, people, process_operations, providers, qms_changes, qms_scope,
    quality_objectives, risks, roles_responsibilities, satisfaction, stakeholders, training,
    training_resources,
)
from ..services.policy import Resource


@dataclass(frozen=True)
class FieldDef:
    """One writable field or list filter, as described to an agent.

    ``type`` is ``string``, ``date`` (YYYY-MM-DD), ``datetime`` (ISO 8601),
    ``integer``, ``integer_list`` (a JSON array of integers), ``number`` (a
    JSON number or decimal text; records return it as decimal text with four
    decimals, e.g. ``"95.0000"``), ``boolean``, ``enum`` (one of ``allowed``) or
    ``object`` (a JSON object, shaped as ``note`` says).
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


def _process_extras(process: Any) -> dict[str, Any]:
    return {"relacionados": process_operations.related_ids(object_session(process),
                                                           process.id_proceso)}


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


def _provider_extras(provider: Any) -> dict[str, Any]:
    """The linked processes, the average score, the latest result and whether it is due."""
    session = object_session(provider)
    latest = providers.latest_evaluation(session, provider.id)
    return {"procesos": provider.proceso_ids,
            "puntuacion_media": providers.average_score(session, provider.id),
            "ultimo_resultado": None if latest is None else providers.resultado(
                providers.puntuacion(latest)),
            "evaluacion_vencida": providers.evaluacion_vencida(provider, today=local_today())}


def _evaluation_extras(evaluation: Any) -> dict[str, Any]:
    score = providers.puntuacion(evaluation)
    return {"puntuacion": score, "resultado": providers.resultado(score)}


def _issue_extras(issue: Any) -> dict[str, Any]:
    """Whether the review is overdue and the ids of the risks and opportunities citing it."""
    return {"revision_vencida": context_issues.revision_vencida(issue, today=local_today()),
            **context_issues.linked_ids(object_session(issue), issue.id)}


def _scope_extras(scope: Any) -> dict[str, Any]:
    """Whether each requirement of the catalogue applies to the version, and why not."""
    return {"aplicabilidad": qms_scope.applicability_of(object_session(scope), scope.id)}


def _change_extras(change: Any) -> dict[str, Any]:
    """The processes and documents the change affects and whether it is past its plan."""
    return {"procesos": change.proceso_ids, "documentos": change.documento_ids,
            "fuera_de_plazo": qms_changes.fuera_de_plazo(change, today=local_today())}


def _change_task_extras(task: Any) -> dict[str, Any]:
    change = object_session(task).get(CambioSGC, task.cambio_id)
    return {"vencida": qms_changes.is_overdue(task, change, today=local_today())}


class _ScopeVersions:
    """The QMS scope's versions as a register: create starts a draft, update edits it.

    Approving and deleting drafts are web-only (OC4).
    """

    def get(self, session: Any, actor: Any, record_id: int) -> Any:
        return qms_scope.get(session, actor, record_id)

    def list_page(self, session: Any, actor: Any, **kwargs: Any) -> tuple[list[Any], int]:
        return qms_scope.list_page(session, actor, **kwargs)

    def create(self, session: Any, actor: Any, data: Any) -> Any:
        return qms_scope.new_draft(session, actor, data)

    def update(self, session: Any, actor: Any, record_id: int, data: Any) -> Any:
        return qms_scope.update_draft(session, actor, record_id, data)


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


class _DatedLists(_DatedRegister):
    """Passes the adapter's date to the lists only; the writes take none."""

    def create(self, session: Any, actor: Any, data: Any) -> Any:
        return self.register.create(session, actor, data)

    def update(self, session: Any, actor: Any, record_id: int, data: Any) -> Any:
        return self.register.update(session, actor, record_id, data)


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
        _f("cuestion_id", "integer", note=(
            "Context issue (cuestiones_contexto) that gives rise to it; vigente when newly "
            "linked.")),
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
        _f("cuestion_id", "integer"), _f("origen", "enum", members=OrigenRiesgo),
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


def _provider_modules() -> tuple[Module, ...]:
    """External providers and their evaluations (8.4), sharing ``EXTERNAL_PROVIDERS`` (EP5).

    Enums show the Spanish label; the filters take the member name. Provider records show
    ``estado`` and its decision columns, which no tool writes: approving, conditioning
    and blocking are web-only, for administrators (EP2). They add ``procesos``,
    ``puntuacion_media`` (over every evaluation), ``ultimo_resultado`` (of the latest
    evaluation) and ``evaluacion_vencida``; evaluation records add ``puntuacion`` (the
    mean of the four scores, two decimals) and ``resultado`` (apto from 3.5,
    condicionado from 2.5, no_apto below).
    """
    return (
        Module("proveedores", "External providers", Resource.EXTERNAL_PROVIDERS,
               _DatedLists(providers.providers), (
            _f("nombre", required=True, note="Unique."),
            _f("nif", note=(
                "Tax id, unique when given; stored upper case without spaces or hyphens, "
                "8 to 10 letters or digits.")),
            _f("contacto", note="Contact person."), _f("email"), _f("telefono"),
            _f("tipo_suministro", "enum", True, members=TipoSuministro),
            _f("descripcion_suministro", note="What it supplies."),
            _f("criterios_seleccion", note="Selection criteria."),
            _f("fecha_proxima_evaluacion", "date", note=(
                "Next evaluation; the evaluations' fecha_proxima moves it (see "
                "evaluaciones_proveedor), and a date edited here stays until one does.")),
            _f("notas"),
            _f("procesos", "integer_list", note=(
                "Processes (procesos) it affects; replaces them.")),
        ), (_f("nombre"), _f("estado", "enum", members=EstadoProveedor),
            _f("tipo_suministro", "enum", members=TipoSuministro),
            _f("proceso_id", "integer"),
            _f("evaluacion_vencida", "boolean", note=(
                "Not bloqueado, with fecha_proxima_evaluacion before today.")),
            _f("ultimo_resultado", "enum", members=ResultadoEvaluacion, note=(
                "Result of the latest evaluation."))),
            extras=_provider_extras),
        Module("evaluaciones_proveedor", "External provider evaluations",
               Resource.EXTERNAL_PROVIDERS, _DatedWrites(providers.evaluations), (
            _f("proveedor_id", "integer", True, note=(
                "Provider (proveedores) evaluated; it cannot change later.")),
            _f("fecha", "date", True, note="Not in the future."),
            _f("evaluador_id", "integer", True, note="Person (personas) who evaluates."),
            *(_f(score, "integer", True, note="1 to 5.")
              for score in ("calidad", "plazo", "servicio", "documentacion")),
            _f("comentario"),
            _f("fecha_proxima", "date", note=(
                "Next evaluation, after fecha. Recording the provider's latest evaluation "
                "(by fecha, then id) with it sets the provider's fecha_proxima_evaluacion; "
                "an older one recorded later does not. Afterwards the provider follows the "
                "latest evaluation that has one: an update or delete that changes that date "
                "moves it there, and it keeps its own date when none remains; a write that "
                "leaves that date alone keeps a date edited on the provider.")),
        ), (_f("proveedor_id", "integer"), _f("evaluador_id", "integer")),
            extras=_evaluation_extras),
    )


def _customer_modules() -> tuple[Module, ...]:
    """Customer master data (8.2) and complaints (9.1.2).

    Records show Spanish enum labels; filters take member names. Complaint
    lifecycle columns, including estado, are read-only: all transitions are web-only.
    """
    return (
        Module("clientes", "Customers", Resource.CUSTOMERS, clientes, (
            _f("nombre", required=True, note="Unique customer name."),
            _f("nif", note="Unique when given; 8–10 letters/digits, upper case, no spaces/hyphens."),
            _f("contacto"), _f("email"), _f("telefono"), _f("notas"),
        ), (_f("nombre"),)),
        Module("reclamaciones", "Complaints", Resource.COMPLAINTS, _DatedWrites(reclamaciones), (
            _f("cliente_id", "integer", True, note="Customer (clientes)."),
            _f("fecha_recepcion", "date", note="Defaults to today; never in the future."),
            _f("descripcion", required=True),
            _f("canal", "enum", members=CanalContacto),
            _f("no_conformidad_id", "integer", note="Optional linked non-conformity."),
        ), (_f("cliente_id", "integer"), _f("estado", "enum", members=EstadoReclamacion),
            _f("canal", "enum", members=CanalContacto))),
    )


def _context_modules() -> tuple[Module, ...]:
    """Context issues (4.1) and the QMS scope (4.3), sharing ``ORGANIZATION_CONTEXT`` (OC4).

    Enums show the Spanish label; the filters take the member name. Issue records add
    ``revision_vencida`` and ``riesgos`` and ``oportunidades``, the ids of the risks
    and opportunities that cite the issue (riesgos and oportunidades, cuestion_id).
    Scope records show ``version``, ``estado`` and the approval columns, which no tool
    writes, and add ``aplicabilidad``. Creating starts the next draft; approving it and
    deleting it are web-only, for administrators (OC3).
    """
    external, internal = (", ".join(member.name for member in CATEGORIAS_POR_TIPO[tipo])
                          for tipo in (TipoContexto.externa, TipoContexto.interna))
    codes = ", ".join(code for code, _ in qms_scope.CATALOGUE)
    return (
        Module("cuestiones_contexto", "Context issues", Resource.ORGANIZATION_CONTEXT,
               _DatedRegister(context_issues.issues), (
            _f("tipo", "enum", True, members=TipoContexto),
            _f("categoria", "enum", True, members=CategoriaCuestion, note=(
                f"Matches tipo: externa takes {external}; interna takes {internal}.")),
            _f("titulo", required=True), _f("descripcion", required=True),
            _f("efecto", "enum", True, members=EfectoCuestion),
            _f("pertinente", "boolean", True, note="Whether the issue is relevant to the QMS."),
            _f("justificacion_pertinencia", note="Why it is or is not relevant; required "
               "when pertinente is false."),
            _f("cambio_climatico", "boolean", note="Concerns climate change; false by default."),
            _f("responsable_id", "integer", note="Person (personas) who owns the issue."),
            _f("fecha_identificacion", "date", True, note="Never in the future."),
            _f("fecha_revision", "date", note="Next review; not before fecha_identificacion."),
            _f("estado", "enum", members=EstadoCuestion, note=(
                "Starts vigente; superada when it no longer applies.")),
            _f("notas"),
        ), (_f("tipo", "enum", members=TipoContexto),
            _f("categoria", "enum", members=CategoriaCuestion),
            _f("pertinente", "boolean"), _f("cambio_climatico", "boolean"),
            _f("estado", "enum", members=EstadoCuestion), _f("responsable_id", "integer"),
            _f("revision_vencida", "boolean", note="Vigente, with fecha_revision before today.")),
            extras=_issue_extras),
        Module("alcances_sgc", "QMS scope", Resource.ORGANIZATION_CONTEXT, _ScopeVersions(), (
            _f("declaracion", note=(
                "Scope statement. Required for the first version; a new draft starts as a "
                "copy of the current version, so only the changes are needed.")),
            _f("productos_servicios", note="Products and services covered."),
            _f("centros", note="Sites covered."),
            _f("notas"),
            _f("aplicabilidad", "object", note=(
                'Changes to the applicability, by requirement: {"8.3": {"aplica": false, '
                '"justificacion": "..."}}; give only the requirements and keys that change. '
                "A requirement that does not apply needs a justification. Requirements: "
                f"{codes}.")),
        ), (_f("estado", "enum", members=EstadoAlcance, note=(
            "borrador: being prepared (at most one); vigente: in force (at most one); "
            "obsoleto: replaced.")),),
            extras=_scope_extras),
    )


def _change_modules() -> tuple[Module, ...]:
    """Changes to the QMS (6.3) and their tasks, sharing ``QMS_CHANGES`` (CP5).

    Enums show the Spanish label; the filters take the member name. Change records show
    ``estado`` and the columns of each stage, which no tool writes: approving, implementing,
    reviewing the effectiveness and cancelling are web-only (CP2), and so is deleting. They
    add ``procesos``, ``documentos`` and ``fuera_de_plazo``; task records add ``vencida``.
    """
    link = "{record}; only with origen {origin}.".format
    consideration = "{what} (6.3 {letter}); needed to approve the change.".format
    return (
        Module("cambios_sgc", "Changes to the QMS", Resource.QMS_CHANGES,
               _DatedRegister(qms_changes.changes), (
            _f("titulo", required=True, note=(
                "With descripcion, origen and its link, proposito, fecha_propuesta and the four "
                "considerations, fixed once the change is aprobado; nothing changes once it is "
                "implantado, eficacia_revisada or cancelado.")),
            _f("descripcion", required=True),
            _f("origen", "enum", True, members=OrigenCambio, note=(
                "What gives rise to the change; only the link of this origin may be given. "
                "Clear the link before changing the origin.")),
            _f("accion_revision_id", "integer", note=link(
                record="Management review action (acciones_revision) of tipo cambio_sgc",
                origin="revision_direccion")),
            _f("no_conformidad_id", "integer", note=link(
                record="Non-conformity (no_conformidades)", origin="no_conformidad")),
            _f("auditoria_id", "integer", note=link(record="Audit (auditorias)",
                                                    origin="auditoria")),
            _f("riesgo_id", "integer", note=link(record="Risk (riesgos)", origin="riesgo")),
            _f("oportunidad_id", "integer", note=link(record="Opportunity (oportunidades)",
                                                      origin="oportunidad")),
            _f("cuestion_id", "integer", note=link(
                record="Context issue (cuestiones_contexto), vigente when newly linked",
                origin="cuestion_contexto")),
            _f("proposito", required=True, note="Purpose of the change (6.3 a)."),
            _f("consecuencias", note=consideration(what="Its potential consequences",
                                                   letter="a")),
            _f("integridad", note=consideration(what="How the integrity of the QMS is kept",
                                                letter="b")),
            _f("recursos", note=consideration(what="Resources available", letter="c")),
            _f("responsabilidades", note=consideration(
                what="Responsibilities and authorities", letter="d")),
            _f("comunicacion", note="To whom and how the change is communicated."),
            _f("responsable_id", "integer", True, note=(
                "Person (personas) who owns the change; never its effectiveness reviewer.")),
            _f("fecha_propuesta", "date", True, note="Never in the future."),
            _f("fecha_prevista", "date", note=(
                "Planned implementation; not before fecha_propuesta. Needed to approve.")),
            _f("procesos", "integer_list", note="Processes (procesos) it affects; replaces them."),
            _f("documentos", "integer_list", note=(
                "Documents (documentos) it affects, withdrawn ones too; replaces them.")),
        ), (_f("estado", "enum", members=EstadoCambio, note=(
                "Starts propuesto. Approving (aprobado) and cancelling (cancelado) are done on "
                "the web by administrators; recording the implementation (implantado) and the "
                "effectiveness review (eficacia_revisada), on the web by administrators and "
                "auditors.")),
            _f("origen", "enum", members=OrigenCambio), _f("responsable_id", "integer"),
            _f("proceso_id", "integer"), _f("documento_id", "integer"),
            _f("fuera_de_plazo", "boolean", note="Aprobado, with fecha_prevista before today."),
            _f("texto", note="Text in titulo or descripcion, any case.")),
            extras=_change_extras),
        Module("tareas_cambio", "Tasks of changes to the QMS", Resource.QMS_CHANGES,
               _DatedRegister(qms_changes.tasks), (
            _f("cambio_id", "integer", True, note=(
                "Change (cambios_sgc) the task carries out; it cannot change later. Tasks are "
                "created, changed and deleted only while the change is propuesto or "
                "aprobado.")),
            _f("descripcion", required=True),
            _f("responsable_id", "integer", True, note="Person (personas) who owns the task."),
            _f("fecha_limite", "date", True),
            _f("estado", "enum", members=EstadoTareaCambio, note=(
                "Starts pendiente. Implementing the change needs every task completada or "
                "cancelada, one of them completada.")),
            _f("fecha_completada", "date", note=(
                "Given exactly when estado is completada; not in the future.")),
            _f("resultado"),
        ), (_f("cambio_id", "integer"), _f("estado", "enum", members=EstadoTareaCambio),
            _f("responsable_id", "integer"),
            _f("vencida", "boolean", note=(
                "Pendiente or en_curso past fecha_limite, in a propuesto or aprobado change."))),
            extras=_change_task_extras),
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
        _f("proceso_id", "integer", note="Process (procesos) the nonconformity concerns"),
        _f("tratamiento", "enum", members=TratamientoSalidaNoConforme, note=(
            "concesion requires authority and date; other or null treatments forbid both.")),
        _f("concesion_autorizada_por_id", "integer", note=(
            "Person (personas) who authorizes the concession")),
        _f("fecha_concesion", "date"), _f("justificacion_concesion"),
        _f("proveedor_id", "integer", note=(
            "External provider (proveedores) that caused it; only with origen proveedor. "
            "Clear it before changing the origin.")),
    ), (_f("descripcion"), _f("estado", "enum", members=EstadoNoConformidad),
        _f("fecha_detectada", "date"), _f("origen", "enum", members=OrigenNoConformidad),
        _f("gravedad", "enum", members=GravedadNoConformidad),
        _f("proveedor_id", "integer"))),
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
    *_customer_modules(),
    Module("satisfaccion_clientes", "Customer satisfaction", Resource.CUSTOMER_SATISFACTION,
           satisfaction, (
        _f("cliente_id", "integer", True, note="Customer (clientes)."),
        _f("tipo_encuesta", "enum", True, members=TipoEncuesta),
        _f("canal", "enum", True, members=CanalContacto),
        _f("fecha_encuesta", "date", True),
        _f("puntuacion", "integer", True), _f("comentarios"),
    ), (_f("cliente_id", "integer"), _f("puntuacion_minima", "integer"),
        _f("tipo_encuesta", "enum", members=TipoEncuesta),
        _f("canal", "enum", members=CanalContacto))),
    # Their writes take the adapter's date: a new fecha_revision is never before it (OC2).
    Module("partes_interesadas", "Interested parties", Resource.INTERESTED_PARTIES,
           _DatedWrites(stakeholders), (
        _f("nombre", required=True), _f("necesidades_expectativas"),
        _f("requisitos_identificados"), _f("objetivo_estrategico"),
        _f("tipo", "enum", members=TipoContexto),
        _f("pertinente", "boolean", note="Whether it is relevant; null means not evaluated."),
        _f("responsable_id", "integer", note="Person (personas) who owns the party."),
        _f("fecha_revision", "date", note="Next review; a new date is not before today."),
        _f("requisitos_climaticos", "boolean", note=(
            "Has climate-related requirements; false by default.")),
    )),
    Module("mejoras", "Improvements", Resource.IMPROVEMENTS, _DatedWrites(improvements), (
        _f("descripcion", required=True),
        _f("origen", "enum", members=OrigenMejora, note="Defaults to otro."),
        _f("responsable_id", "integer", note="Active person who owns the improvement."),
        _f("fecha_identificacion", "date", required=True, note="Not after today."),
        _f("beneficio"),
        *(_f(column, "integer", note=f"Optional origin link; requires origen={origin.name}.")
          for column, (origin, _) in improvements.MEJORA_LINKS.items()),
    ), filters=(
        _f("estado", "enum", members=EstadoMejora, note=(
            "Read-only lifecycle state; transitions are web-only.")),
        _f("origen", "enum", members=OrigenMejora),
        _f("responsable_id", "integer"),
    )),
    Module("roles_responsabilidades", "Roles and responsibilities",
           Resource.ROLES_RESPONSIBILITIES, roles_responsibilities, (
        _f("rol", required=True), _f("compromiso_calidad", "boolean"),
        _f("descripcion_politica_calidad"), _f("responsabilidades"), _f("autoridades"),
        _f("reporta_a_id", "integer", note=(
            "Role (roles_responsabilidades) it reports to; never itself nor a role that "
            "reports to it, directly or not. A role others report to cannot be deleted.")),
    )),
    Module("recursos_capacitacion", "Training resources", Resource.TRAINING_RESOURCES,
           training_resources, (
        _f("recurso_necesario", required=True), _f("capacitacion_personal", "boolean"),
        _f("descripcion_documentacion"),
    )),
    # Records add ``relacionados``; ``tipo`` shows the Spanish label, the filter takes the
    # member name.
    Module("procesos", "Process operations", Resource.PROCESS_OPERATIONS,
           process_operations, (
        _f("proceso", required=True), _f("tipo", "enum", members=TipoProceso),
        _f("responsable_id", "integer", note="Person (personas) who owns the process."),
        _f("entradas"), _f("salidas"), _f("recursos"),
        _f("criterio_calidad", note="Operating criteria (8.1)."),
        _f("criterios_aceptacion", note="Acceptance criteria of its outputs."),
        _f("control_proveedor", "boolean"), _f("no_conformidad"),
        _f("relacionados", "integer_list", note=(
            "Processes (procesos) it interacts with; replaces them. The relation is mutual: "
            "each related process lists this one too.")),
    ), (_f("tipo", "enum", members=TipoProceso), _f("responsable_id", "integer")),
        extras=_process_extras),
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
    *_provider_modules(),
    *_context_modules(),
    *_change_modules(),
    Module("confirmaciones_lectura", "Read confirmations", Resource.READ_CONFIRMATIONS,
           _DatedWrites(read_confirmations), (
        _f("persona_id", "integer", True, note="Existing person (personas), active when newly chosen."),
        _f("tipo", "enum", True, members=TipoConfirmacion),
        _f("fecha_confirmacion", "date", True, note="Not in the future."),
        _f("document_revision_id", "integer", note=(
            "Existing document revision; required with documento, forbidden with objetivos.")),
        _f("notas"),
    ), (_f("persona_id", "integer"), _f("tipo", "enum", members=TipoConfirmacion),
        _f("document_revision_id", "integer"))),
)

MODULES: dict[str, Module] = {module.slug: module for module in _MODULES}
