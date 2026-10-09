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

from ..models import (
    CompetenceEvaluation, CompetenceType, DocumentCategory, EstadoAuditoriaEnum,
    EstadoNoConformidad, GravedadNoConformidad, OrigenNoConformidad, TipoEnum,
)
from ..services import (
    audit_indicators, audits, competence, documents, improvements, nonconformities, people,
    process_operations, risks_opportunities, roles_responsibilities,
    satisfaction, stakeholders, training, training_resources,
)
from ..services.policy import Resource


@dataclass(frozen=True)
class FieldDef:
    """One writable field or list filter, as described to an agent.

    ``type`` is ``string``, ``date`` (YYYY-MM-DD), ``datetime`` (ISO 8601),
    ``integer``, ``integer_list`` (a JSON array of integers), ``boolean`` or
    ``enum`` (one of ``allowed``). ``members`` names the Python enum a filter
    must be turned into before the service sees it; without it an enum filter
    is passed on as the plain string. ``note`` is a short hint shown to the
    agent when the type and flags do not say enough.
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


# ``responsable_id``, ``auditor_id`` and ``persona_id`` are ids of ``personas``
# records (an existing person, active when newly chosen; ``null`` clears the
# link). The free-text ``responsable``, ``auditor`` and ``personal`` stay as
# legacy names and are never matched to a person. ``auditor`` and ``personal``
# are not flagged required: the services require them only when no person is
# cited (``people.fill_name``), and their note says so.
_MODULES = (
    # ``estado`` is not writable: a record starts "abierta" and only the service's
    # explicit transitions move it (records show the Spanish label, filters take
    # the member name). A closed or cancelled record refuses updates.
    Module("no_conformidades", "Non-conformities", Resource.NONCONFORMITIES, nonconformities, (
        _f("descripcion", required=True), _f("fecha_detectada", "date", True),
        _f("origen", "enum", members=OrigenNoConformidad),
        _f("gravedad", "enum", members=GravedadNoConformidad),
        _legacy_name("responsable", "responsable_id"), _f("responsable_id", "integer"),
        _f("contencion"), _f("causa_raiz"),
        _f("accion_correctiva", note="Legacy free-text corrective action."),
    ), (_f("descripcion"), _f("estado", "enum", members=EstadoNoConformidad),
        _f("fecha_detectada", "date"))),
    Module("auditorias", "Audits", Resource.AUDITS, audits, (
        _f("area_auditada", required=True), _f("fecha", "date", True),
        _legacy_name("auditor", "auditor_id"), _f("auditor_id", "integer"),
        _f("resultado", required=True), _f("accion_correctiva"),
        _f("estado", "enum", members=EstadoAuditoriaEnum),
    ), (_f("area"), _f("auditor"), _f("estado", "enum", members=EstadoAuditoriaEnum),
        _f("fecha_inicio", "date"), _f("fecha_fin", "date"))),
    Module("documentos", "Documents", Resource.DOCUMENTS, documents, (
        _f("title", required=True), _f("code", required=True),
        _f("category", "enum", True, DocumentCategory.__members__),
        _f("version", required=True), _f("issued_date", "date", True),
        _f("approved_by"), _f("content", required=True),
    )),
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
    Module("riesgos_oportunidades", "Risks and opportunities",
           Resource.RISKS_OPPORTUNITIES, risks_opportunities, (
        _f("tipo", "enum", True, TipoEnum.__members__), _f("descripcion", required=True),
        _f("objetivo_calidad"), _f("plan_accion"),
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
)

MODULES: dict[str, Module] = {module.slug: module for module in _MODULES}
