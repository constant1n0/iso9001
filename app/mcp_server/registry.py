"""The QMS modules an agent can reach: slug, policy resource, service and fields.

One generic tool surface serves every module, so this table is what tells an
agent (through ``qms_modules``) which fields, allowed values and filters each
module has. Tests keep the declared fields in step with the services.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import ModuleType

from ..models import DocumentCategory, EstadoAuditoriaEnum, TipoEnum
from ..services import (
    audit_indicators, audits, documents, improvements, nonconformities,
    process_operations, risks_opportunities, roles_responsibilities,
    satisfaction, stakeholders, training, training_resources,
)
from ..services.nonconformities import ESTADOS_NO_CONFORMIDAD
from ..services.policy import Resource


@dataclass(frozen=True)
class FieldDef:
    """One writable field or list filter, as described to an agent.

    ``type`` is ``string``, ``date`` (YYYY-MM-DD), ``datetime`` (ISO 8601),
    ``integer``, ``boolean`` or ``enum`` (one of ``allowed``). ``members`` names
    the Python enum a filter must be turned into before the service sees it;
    without it an enum filter is passed on as the plain string.
    """

    name: str
    type: str = "string"
    required: bool = False
    allowed: tuple[str, ...] = ()
    members: type[Enum] | None = None


@dataclass(frozen=True)
class Module:
    slug: str
    label: str
    resource: Resource
    service: ModuleType
    fields: tuple[FieldDef, ...]
    filters: tuple[FieldDef, ...] = ()
    # True when ``service.list_page`` pages in the database; otherwise ``list_``
    # loads every row and ``qms_list`` slices in memory (follow-up: page those
    # services in SQL too). A test keeps this in step with the service.
    paged_in_db: bool = False


def _f(name: str, type: str = "string", required: bool = False, allowed=(), members=None) -> FieldDef:
    if members is not None:
        allowed = members.__members__
    return FieldDef(name, type, required, tuple(allowed), members)


_MODULES = (
    Module("no_conformidades", "Non-conformities", Resource.NONCONFORMITIES, nonconformities, (
        _f("descripcion", required=True), _f("fecha_detectada", "date", True),
        _f("responsable"), _f("estado", "enum", allowed=ESTADOS_NO_CONFORMIDAD),
        _f("accion_correctiva"),
    # The state filter takes the fixed states only: stored legacy free-text states
    # are still listed but cannot be filtered on (writes keep a record's current
    # legacy state); nonconformities.available_states shows them to humans.
    ), (_f("descripcion"), _f("estado", "enum", allowed=ESTADOS_NO_CONFORMIDAD),
        _f("fecha_detectada", "date"))),
    Module("auditorias", "Audits", Resource.AUDITS, audits, (
        _f("area_auditada", required=True), _f("fecha", "date", True),
        _f("auditor", required=True), _f("resultado", required=True),
        _f("accion_correctiva"), _f("estado", "enum", members=EstadoAuditoriaEnum),
    ), (_f("area"), _f("auditor"), _f("estado", "enum", members=EstadoAuditoriaEnum),
        _f("fecha_inicio", "date"), _f("fecha_fin", "date")), paged_in_db=True),
    Module("documentos", "Documents", Resource.DOCUMENTS, documents, (
        _f("title", required=True), _f("code", required=True),
        _f("category", "enum", True, DocumentCategory.__members__),
        _f("version", required=True), _f("issued_date", "date", True),
        _f("approved_by"), _f("content", required=True),
    )),
    Module("capacitaciones", "Training", Resource.TRAINING, training, (
        _f("tema", required=True), _f("fecha", "date", True), _f("personal", required=True),
        _f("duracion_horas", "integer"), _f("evaluacion_final"),
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
    ), paged_in_db=True),
    Module("roles_responsabilidades", "Roles and responsibilities",
           Resource.ROLES_RESPONSIBILITIES, roles_responsibilities, (
        _f("rol", required=True), _f("compromiso_calidad", "boolean"),
        _f("descripcion_politica_calidad"),
    ), paged_in_db=True),
    Module("riesgos_oportunidades", "Risks and opportunities",
           Resource.RISKS_OPPORTUNITIES, risks_opportunities, (
        _f("tipo", "enum", True, TipoEnum.__members__), _f("descripcion", required=True),
        _f("objetivo_calidad"), _f("plan_accion"),
    ), paged_in_db=True),
    Module("recursos_capacitacion", "Training resources", Resource.TRAINING_RESOURCES,
           training_resources, (
        _f("recurso_necesario", required=True), _f("capacitacion_personal", "boolean"),
        _f("descripcion_documentacion"),
    ), paged_in_db=True),
    Module("procesos", "Process operations", Resource.PROCESS_OPERATIONS,
           process_operations, (
        _f("proceso", required=True), _f("criterio_calidad"),
        _f("control_proveedor", "boolean"), _f("no_conformidad"),
    ), paged_in_db=True),
    Module("indicadores_auditoria", "Audit indicators", Resource.AUDIT_INDICATORS,
           audit_indicators, (
        _f("area_auditoria", required=True), _f("fecha_auditoria", "datetime"),
        _f("resultado"), _f("accion_correctiva"), _f("indicador_desempeno"),
    ), paged_in_db=True),
)

MODULES: dict[str, Module] = {module.slug: module for module in _MODULES}
