"""Changes to the QMS and their tasks: models, CHECK constraints and links (CP-1).

ISO 9001 clause 6.3, decisions CP1 to CP4 of ``change-planning``. A change
(``CambioSGC``) has an origin and may name the record behind it, each link
allowed only with its own origin; it records its purpose and the 6.3
considerations, an owner, and moves ``propuesto`` -> ``aprobado`` ->
``implantado`` -> ``eficacia_revisada`` or ends ``cancelado``. The CHECK
constraints keep each stage's columns to the states that carry them. Its tasks
(``TareaCambio``) carry a completion date only when completed, and the plain
tables ``cambio_procesos`` and ``cambio_documentos`` hold the processes and
documents it affects. The constraint cases run on SQLite with ``PRAGMA
foreign_keys=ON`` and on PostgreSQL when ``TEST_POSTGRES_URI`` is set (CI
provides it).
"""

from __future__ import annotations

import os
import unittest
from datetime import date

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase

from app.extensions import db
from app.models import (
    Auditoria, CuestionContexto, Document, DocumentCategory, NoConformidad, Oportunidad,
    Person, ProcesoOperacion, RevisionDireccion, Riesgo,
)

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
CHANGE = {"titulo": "Nuevo ERP", "descripcion": "Sustituir la hoja de pedidos por un ERP",
          "origen": "otro", "proposito": "Trazabilidad de los pedidos",
          "fecha_propuesta": date(2026, 10, 1)}
TASK = {"descripcion": "Migrar los pedidos abiertos", "fecha_limite": date(2026, 12, 15)}
# Each link to the record behind a change, the origin it needs and the table it cites.
LINKS = {
    "accion_revision_id": ("revision_direccion", "acciones_revision"),
    "no_conformidad_id": ("no_conformidad", "no_conformidades"),
    "auditoria_id": ("auditoria", "auditorias"),
    "riesgo_id": ("riesgo", "riesgos"),
    "oportunidad_id": ("oportunidad", "oportunidades"),
    "cuestion_id": ("cuestion_contexto", "cuestiones_contexto"),
}
APPROVED = "aprobado_por_id = :eva, fecha_aprobacion = '2026-10-10'"
IMPLEMENTED = f"{APPROVED}, fecha_implantacion = '2026-11-20'"
REVIEWED = (f"{IMPLEMENTED}, eficacia = 'eficaz', fecha_eficacia = '2027-01-15', "
            "revisor_eficacia_id = :eva")
CANCELLED = "motivo_cancelacion = 'Ya no es necesario', fecha_cancelacion = '2026-10-20'"


def models():
    import app.models as module

    return module


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert: these tests must not depend on the services CP-2 adds."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str = "Ana Pérez") -> int:
        return self.insert(Person.__table__, nombre=nombre)

    def change(self, **values) -> int:
        if "responsable_id" not in values:
            values["responsable_id"] = self.person("Responsable del cambio")
        return self.insert(models().CambioSGC.__table__, **(CHANGE | values))

    def task(self, cambio_id: int, **values) -> int:
        if "responsable_id" not in values:
            values["responsable_id"] = self.person("Responsable de la tarea")
        return self.insert(models().TareaCambio.__table__,
                           **(TASK | {"cambio_id": cambio_id} | values))

    def origins(self, owner: int) -> dict[str, int]:
        """One record of each kind a change may name, keyed by the change's column."""
        review = self.insert(RevisionDireccion.__table__, fecha=date(2026, 7, 10),
                             periodo_desde=date(2026, 4, 1), periodo_hasta=date(2026, 6, 30))
        return {
            "accion_revision_id": self.insert(
                models().AccionRevision.__table__, revision_id=review, tipo="cambio_sgc",
                descripcion="Implantar un ERP", responsable_id=owner,
                fecha_limite=date(2026, 12, 31)),
            "no_conformidad_id": self.insert(NoConformidad.__table__, descripcion="Pedido perdido",
                                             fecha_detectada=date(2026, 9, 1)),
            "auditoria_id": self.insert(Auditoria.__table__, area_auditada="Ventas",
                                        fecha=date(2026, 9, 2), auditor="Luis",
                                        resultado="Sin trazabilidad"),
            "riesgo_id": self.insert(Riesgo.__table__, titulo="Pérdida de pedidos",
                                     descripcion="Hoja compartida", origen="interno",
                                     fecha_identificacion=date(2026, 9, 3)),
            "oportunidad_id": self.insert(Oportunidad.__table__, titulo="Venta en línea",
                                          descripcion="Pedidos web", origen="externo",
                                          fecha_identificacion=date(2026, 9, 4)),
            "cuestion_id": self.insert(CuestionContexto.__table__, tipo="interna",
                                       categoria="conocimiento", titulo="Saber disperso",
                                       descripcion="Cada comercial lleva su hoja",
                                       efecto="negativo", pertinente=True,
                                       fecha_identificacion=date(2026, 9, 5)),
        }

    def seed(self) -> dict[str, int]:
        """An owner (Ana), an approver and reviewer (Eva), a change Ana owns with one task,
        and a process and a document it affects."""
        ids = {"ana": self.person("Ana"), "eva": self.person("Eva")}
        ids["change"] = self.change(responsable_id=ids["ana"])
        ids["task"] = self.task(ids["change"], responsable_id=ids["ana"])
        ids["process"] = self.insert(ProcesoOperacion.__table__, proceso="Ventas")
        ids["document"] = self.insert(Document.__table__, title="Procedimiento de pedidos",
                                      code="PR-07", category=DocumentCategory.OTRO)
        db.session.execute(models().cambio_procesos.insert().values(
            cambio_id=ids["change"], proceso_id=ids["process"]))
        db.session.execute(models().cambio_documentos.insert().values(
            cambio_id=ids["change"], documento_id=ids["document"]))
        db.session.commit()
        return ids

    def rows(self, sql: str, params: dict | None = None) -> list[tuple]:
        return [tuple(row) for row in db.session.execute(text(sql), params or {})]

    def accepted(self, params: dict, *statements: str) -> None:
        for statement in statements:
            db.session.execute(text(statement), params)
        db.session.commit()

    def assert_refused(self, params: dict, *statements: str) -> None:
        for statement in statements:
            with self.subTest(refused=statement):
                with self.assertRaises(IntegrityError):
                    db.session.execute(text(statement), params)
                    db.session.commit()
                db.session.rollback()


class ChoicesTestCase(unittest.TestCase):
    def test_choices_are_stored_by_name_and_shown_in_spanish(self) -> None:
        m = models()
        for enum_cls, labels in (
            (m.OrigenCambio, {"revision_direccion": "Revisión por la dirección",
                              "no_conformidad": "No conformidad", "auditoria": "Auditoría",
                              "riesgo": "Riesgo", "oportunidad": "Oportunidad",
                              "cuestion_contexto": "Cuestión del contexto", "otro": "Otro"}),
            (m.EstadoCambio, {"propuesto": "Propuesto", "aprobado": "Aprobado",
                              "implantado": "Implantado",
                              "eficacia_revisada": "Eficacia revisada",
                              "cancelado": "Cancelado"}),
            (m.EficaciaCambio, {"eficaz": "Eficaz", "no_eficaz": "No eficaz"}),
            (m.EstadoTareaCambio, {"pendiente": "Pendiente", "en_curso": "En curso",
                                   "completada": "Completada", "cancelada": "Cancelada"}),
        ):
            with self.subTest(enum=enum_cls.__name__):
                self.assertEqual(labels, {member.name: member.value for member in enum_cls})

    def test_every_origin_link_names_its_origin_and_its_target(self) -> None:
        m = models()
        self.assertEqual(
            {column: (m.OrigenCambio[origin], f"{table}.id")
             for column, (origin, table) in LINKS.items()},
            m.CHANGE_LINKS)
        self.assertEqual({m.OrigenCambio.otro},
                         set(m.OrigenCambio) - {origin for origin, _ in m.CHANGE_LINKS.values()})


class SchemaTestCase(unittest.TestCase):
    """Columns, foreign keys and indexes as the models declare them."""

    def test_links_are_indexed_restrict_foreign_keys(self) -> None:
        m = models()
        cases = [(m.CambioSGC, column, f"{table}.id", True)
                 for column, (_, table) in LINKS.items()]
        cases += [
            (m.CambioSGC, "responsable_id", "personas.id", False),
            (m.CambioSGC, "aprobado_por_id", "personas.id", True),
            (m.CambioSGC, "revisor_eficacia_id", "personas.id", True),
            (m.TareaCambio, "cambio_id", "cambios_sgc.id", False),
            (m.TareaCambio, "responsable_id", "personas.id", False),
        ]
        for model, column, target, nullable in cases:
            table = model.__table__
            with self.subTest(table=table.name, column=column):
                (fk,) = table.c[column].foreign_keys
                self.assertEqual(target, fk.target_fullname)
                self.assertEqual("RESTRICT", fk.ondelete)
                self.assertEqual(f"fk_{table.name}_{column}_{target.split('.')[0]}",
                                 fk.constraint.name)
                self.assertEqual(nullable, table.c[column].nullable)
                self.assertIn([column], [[c.name for c in index.columns]
                                         for index in table.indexes])

    def test_required_columns_lengths_and_defaults(self) -> None:
        m = models()
        metadata = {"id", "created_at", "updated_at", "created_by_id", "updated_by_id"}
        for model, required, state in (
            (m.CambioSGC, {"titulo", "descripcion", "origen", "proposito", "responsable_id",
                           "fecha_propuesta", "estado"}, "propuesto"),
            (m.TareaCambio, {"cambio_id", "descripcion", "responsable_id", "fecha_limite",
                             "estado"}, "pendiente"),
        ):
            table = model.__table__
            with self.subTest(table=table.name):
                self.assertEqual(required, {c.name for c in table.c
                                            if not c.nullable and c.name not in metadata})
                self.assertEqual(state, table.c.estado.server_default.arg)
        columns = m.CambioSGC.__table__.c
        self.assertEqual(200, columns.titulo.type.length)
        for name in ("consecuencias", "integridad", "recursos", "responsabilidades",
                     "comunicacion", "fecha_prevista", "fecha_aprobacion",
                     "fecha_implantacion", "eficacia", "fecha_eficacia",
                     "comentario_eficacia", "motivo_cancelacion", "fecha_cancelacion"):
            with self.subTest(optional=name):
                self.assertTrue(columns[name].nullable)
        self.assertEqual({"fecha_completada", "resultado"},
                         {c.name for c in m.TareaCambio.__table__.c
                          if c.nullable and c.name not in metadata})

    def test_the_link_tables_are_plain_tables_with_composite_keys(self) -> None:
        m = models()
        for table, second, target in ((m.cambio_procesos, "proceso_id",
                                       "procesos_operacion.id_proceso"),
                                      (m.cambio_documentos, "documento_id", "documents.id")):
            with self.subTest(table=table.name):
                self.assertEqual(["cambio_id", second],
                                 [c.name for c in table.primary_key.columns])
                self.assertEqual(f"pk_{table.name}", table.primary_key.name)
                self.assertEqual({("cambios_sgc.id", "RESTRICT"), (target, "RESTRICT")},
                                 {(fk.target_fullname, fk.ondelete)
                                  for fk in table.foreign_keys})
                self.assertEqual([[second]], [[c.name for c in index.columns]
                                              for index in table.indexes])
                self.assertNotIn(table.name, {cls.__tablename__ for cls in
                                              (mapper.class_ for mapper in
                                               db.Model.registry.mappers)})

    def test_the_relationships_are_one_way(self) -> None:
        m = models()
        relationships = {rel.key: rel for rel in db.inspect(m.CambioSGC).relationships}
        self.assertEqual({"accion_revision", "no_conformidad", "auditoria", "riesgo",
                          "oportunidad", "cuestion", "responsable", "aprobado_por",
                          "revisor_eficacia", "procesos", "documentos", "tareas"},
                         set(relationships))
        for name, rel in relationships.items():
            with self.subTest(relationship=name):
                self.assertFalse(rel.backref)
                self.assertIsNone(rel.back_populates)
        self.assertEqual(set(), {rel.key for rel in db.inspect(m.TareaCambio).relationships})
        self.assertEqual("all", relationships["tareas"].passive_deletes)
        change = m.CambioSGC(procesos=[ProcesoOperacion(id_proceso=3),
                                       ProcesoOperacion(id_proceso=1)],
                             documentos=[Document(id=5)])
        self.assertEqual(([1, 3], [5]), (change.proceso_ids, change.documento_ids))


class ConstraintCases:
    """The CHECK constraints and foreign keys; shared by the SQLite and PostgreSQL cases."""

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_a_change_starts_proposed_and_can_run_its_whole_life(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        self.assertEqual([("propuesto", "otro", None, None, None)], self.rows(
            "SELECT estado, origen, aprobado_por_id, fecha_implantacion, eficacia "
            "FROM cambios_sgc"))
        self.assertEqual([("pendiente", None)], self.rows(
            "SELECT estado, fecha_completada FROM tareas_cambio"))
        self.accepted(
            ids,
            "UPDATE cambios_sgc SET consecuencias = 'Formación', integridad = 'Sin pausa', "
            "recursos = 'Licencias', responsabilidades = 'Dirección comercial', "
            "comunicacion = 'Reunión de equipo', fecha_prevista = '2026-11-30'",
            f"UPDATE cambios_sgc SET estado = 'aprobado', {APPROVED}",
            "UPDATE tareas_cambio SET estado = 'completada', fecha_completada = '2026-11-15', "
            "resultado = 'Migrados'",
            f"UPDATE cambios_sgc SET estado = 'implantado', {IMPLEMENTED}",
            f"UPDATE cambios_sgc SET estado = 'eficacia_revisada', {REVIEWED}",
        )
        self.assertEqual([("eficacia_revisada", "eficaz", ids["eva"])], self.rows(
            "SELECT estado, eficacia, revisor_eficacia_id FROM cambios_sgc"))

    def test_each_origin_link_needs_its_own_origin(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        records = self.origins(ids["ana"])
        for column, (origin, _) in LINKS.items():
            params = ids | {"record": records[column], "origin": origin}
            others = [other for other, _ in LINKS.values() if other != origin] + ["otro"]
            with self.subTest(column=column):
                self.assert_refused(
                    params,
                    f"UPDATE cambios_sgc SET {column} = :record",  # origin ``otro``
                    *(f"UPDATE cambios_sgc SET origen = '{other}', {column} = :record"
                      for other in others),
                    "INSERT INTO cambios_sgc (titulo, descripcion, origen, proposito, "
                    f"responsable_id, fecha_propuesta, {column}) VALUES ('T', 'D', "
                    f"'{others[0]}', 'P', :ana, '2026-10-01', :record)",
                )
                self.accepted(params,
                              f"UPDATE cambios_sgc SET origen = :origin, {column} = :record")
                self.assert_refused(params, f"UPDATE cambios_sgc SET origen = '{others[0]}'")
                self.accepted(params,
                              f"UPDATE cambios_sgc SET origen = 'otro', {column} = NULL")
        self.assert_refused(ids, "UPDATE cambios_sgc SET origen = 'Otro'",
                            "UPDATE cambios_sgc SET origen = 'proveedor'",
                            "UPDATE cambios_sgc SET origen = NULL")

    def test_the_approval_checks(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        self.assert_refused(
            ids,
            "UPDATE cambios_sgc SET aprobado_por_id = :eva",  # still proposed
            "UPDATE cambios_sgc SET fecha_aprobacion = '2026-10-10'",
            f"UPDATE cambios_sgc SET {APPROVED}",
            "UPDATE cambios_sgc SET estado = 'aprobado'",
            "UPDATE cambios_sgc SET estado = 'aprobado', aprobado_por_id = :eva",
            "UPDATE cambios_sgc SET estado = 'aprobado', fecha_aprobacion = '2026-10-10'",
            "UPDATE cambios_sgc SET estado = 'Aprobado'",
            f"UPDATE cambios_sgc SET estado = 'cancelado', {CANCELLED}, aprobado_por_id = :eva",
            f"UPDATE cambios_sgc SET estado = 'cancelado', {CANCELLED}, "
            "fecha_aprobacion = '2026-10-10'",
        )
        self.accepted(ids, f"UPDATE cambios_sgc SET estado = 'aprobado', {APPROVED}")
        self.assert_refused(
            ids,
            "UPDATE cambios_sgc SET aprobado_por_id = NULL",
            "UPDATE cambios_sgc SET fecha_aprobacion = NULL",
            "UPDATE cambios_sgc SET estado = 'propuesto'",  # keeping the approval
        )
        # A change may be cancelled before or after its approval.
        self.accepted(ids, f"UPDATE cambios_sgc SET estado = 'cancelado', {CANCELLED}")
        self.accepted(ids, "UPDATE cambios_sgc SET aprobado_por_id = NULL, "
                           "fecha_aprobacion = NULL")
        self.assertEqual([("cancelado", None, None)], self.rows(
            "SELECT estado, aprobado_por_id, fecha_aprobacion FROM cambios_sgc"))

    def test_the_implementation_checks(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        self.assert_refused(
            ids,
            "UPDATE cambios_sgc SET fecha_implantacion = '2026-11-20'",  # proposed
            f"UPDATE cambios_sgc SET estado = 'aprobado', {IMPLEMENTED}",
            f"UPDATE cambios_sgc SET estado = 'implantado', {APPROVED}",  # without its date
            "UPDATE cambios_sgc SET estado = 'implantado', "
            "fecha_implantacion = '2026-11-20'",  # without the approval
        )
        self.accepted(ids, f"UPDATE cambios_sgc SET estado = 'implantado', {IMPLEMENTED}")
        self.assert_refused(
            ids,
            "UPDATE cambios_sgc SET fecha_implantacion = NULL",
            "UPDATE cambios_sgc SET estado = 'aprobado'",  # keeping the date
            f"UPDATE cambios_sgc SET estado = 'cancelado', {CANCELLED}",
        )

    def test_the_effectiveness_checks(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        self.accepted(ids, f"UPDATE cambios_sgc SET estado = 'implantado', {IMPLEMENTED}")
        reviewed = "estado = 'eficacia_revisada', fecha_eficacia = '2027-01-15'"
        self.assert_refused(
            ids,
            "UPDATE cambios_sgc SET estado = 'eficacia_revisada'",
            f"UPDATE cambios_sgc SET {reviewed}, eficacia = 'eficaz'",  # no reviewer
            f"UPDATE cambios_sgc SET {reviewed}, revisor_eficacia_id = :eva",  # no result
            "UPDATE cambios_sgc SET estado = 'eficacia_revisada', eficacia = 'eficaz', "
            "revisor_eficacia_id = :eva",  # no date
            "UPDATE cambios_sgc SET eficacia = 'eficaz', fecha_eficacia = '2027-01-15', "
            "revisor_eficacia_id = :eva",  # still implemented
            f"UPDATE cambios_sgc SET {reviewed}, eficacia = 'no_eficaz', "
            "revisor_eficacia_id = :eva",  # ineffective without a comment
            f"UPDATE cambios_sgc SET {reviewed}, eficacia = 'eficaz', "
            "revisor_eficacia_id = :ana",  # the owner reviews it
            f"UPDATE cambios_sgc SET {reviewed}, eficacia = 'Eficaz', "
            "revisor_eficacia_id = :eva",
            f"UPDATE cambios_sgc SET {reviewed}, eficacia = 'eficaz', "
            "revisor_eficacia_id = :eva, fecha_implantacion = NULL",
            f"UPDATE cambios_sgc SET {reviewed}, eficacia = 'eficaz', "
            "revisor_eficacia_id = 999",
        )
        self.accepted(ids, f"UPDATE cambios_sgc SET {reviewed}, eficacia = 'no_eficaz', "
                           "revisor_eficacia_id = :eva, "
                           "comentario_eficacia = 'Siguen perdiéndose pedidos'")
        self.assert_refused(
            ids,
            "UPDATE cambios_sgc SET comentario_eficacia = NULL",
            "UPDATE cambios_sgc SET estado = 'implantado'",  # keeping the review
            "UPDATE cambios_sgc SET responsable_id = :eva",  # the reviewer would own it
        )
        self.accepted(ids, "UPDATE cambios_sgc SET eficacia = 'eficaz', "
                           "comentario_eficacia = NULL")

    def test_the_cancellation_checks(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        self.assert_refused(
            ids,
            "UPDATE cambios_sgc SET estado = 'cancelado'",
            "UPDATE cambios_sgc SET estado = 'cancelado', "
            "motivo_cancelacion = 'Ya no es necesario'",
            "UPDATE cambios_sgc SET estado = 'cancelado', fecha_cancelacion = '2026-10-20'",
            f"UPDATE cambios_sgc SET {CANCELLED}",  # still proposed
        )
        self.accepted(ids, f"UPDATE cambios_sgc SET estado = 'cancelado', {CANCELLED}")
        self.assert_refused(
            ids,
            "UPDATE cambios_sgc SET motivo_cancelacion = NULL",
            "UPDATE cambios_sgc SET fecha_cancelacion = NULL",
            "UPDATE cambios_sgc SET estado = 'propuesto'",  # keeping the reason
        )

    def test_the_required_columns(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        self.assert_refused(
            ids,
            *(f"UPDATE cambios_sgc SET {column} = NULL"
              for column in ("titulo", "descripcion", "origen", "proposito", "responsable_id",
                             "fecha_propuesta", "estado")),
            *(f"UPDATE tareas_cambio SET {column} = NULL"
              for column in ("cambio_id", "descripcion", "responsable_id", "fecha_limite",
                             "estado")),
        )

    def test_the_task_checks(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        self.assert_refused(
            ids,
            "UPDATE tareas_cambio SET estado = 'completada'",  # without its date
            "UPDATE tareas_cambio SET fecha_completada = '2026-11-15'",  # not completed
            "UPDATE tareas_cambio SET estado = 'en_curso', fecha_completada = '2026-11-15'",
            "UPDATE tareas_cambio SET estado = 'Completada', fecha_completada = '2026-11-15'",
            "UPDATE tareas_cambio SET estado = 'hecha'",
        )
        self.accepted(ids, "UPDATE tareas_cambio SET estado = 'completada', "
                           "fecha_completada = '2026-11-15', resultado = 'Migrados'")
        self.assert_refused(ids, "UPDATE tareas_cambio SET fecha_completada = NULL",
                            "UPDATE tareas_cambio SET estado = 'cancelada'")
        self.accepted(ids, "UPDATE tareas_cambio SET estado = 'cancelada', "
                           "fecha_completada = NULL")

    def test_links_are_real_restrict_foreign_keys(self) -> None:
        self.enforce_foreign_keys()
        ids = self.seed()
        records = self.origins(ids["ana"])
        for column, (origin, _) in LINKS.items():
            self.change(responsable_id=ids["ana"], origen=origin, **{column: records[column]})
        self.assert_refused(
            ids,
            *(f"UPDATE cambios_sgc SET origen = '{origin}', {column} = 999 "
              f"WHERE id = {ids['change']}" for column, (origin, _) in LINKS.items()),
            f"UPDATE cambios_sgc SET responsable_id = 999 WHERE id = {ids['change']}",
            "UPDATE tareas_cambio SET cambio_id = 999",
            "UPDATE tareas_cambio SET responsable_id = 999",
            "INSERT INTO cambio_procesos (cambio_id, proceso_id) VALUES (:change, 999)",
            "INSERT INTO cambio_procesos (cambio_id, proceso_id) VALUES (999, :process)",
            "INSERT INTO cambio_procesos (cambio_id, proceso_id) VALUES (:change, :process)",
            "INSERT INTO cambio_documentos (cambio_id, documento_id) VALUES (:change, 999)",
            "INSERT INTO cambio_documentos (cambio_id, documento_id) VALUES (999, :document)",
            "INSERT INTO cambio_documentos (cambio_id, documento_id) "
            "VALUES (:change, :document)",
            "DELETE FROM procesos_operacion",
            "DELETE FROM documents",
            f"DELETE FROM cambios_sgc WHERE id = {ids['change']}",  # its task and links
            "DELETE FROM personas WHERE id = :ana",
            *(f"DELETE FROM {table}" for _, table in LINKS.values()),
        )
        self.assertEqual([(7, 1, 1, 1, 1, 1, 1, 1)], self.rows(
            "SELECT (SELECT count(*) FROM cambios_sgc), (SELECT count(*) FROM tareas_cambio), "
            "(SELECT count(*) FROM cambio_procesos), (SELECT count(*) FROM cambio_documentos), "
            "(SELECT count(*) FROM acciones_revision), (SELECT count(*) FROM no_conformidades), "
            "(SELECT count(*) FROM auditorias), (SELECT count(*) FROM cuestiones_contexto)"))


class SqliteTestCase(ConstraintCases, Helpers, ServiceBase):
    def tearDown(self) -> None:
        # Dropping a table deletes its rows, which the restricted links refuse.
        db.session.rollback()
        db.session.execute(text("PRAGMA foreign_keys=OFF"))
        db.session.commit()
        super().tearDown()

    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresTestCase(ConstraintCases, Helpers, unittest.TestCase):
    """The same cases against PostgreSQL, whose foreign keys are always enforced."""

    def setUp(self) -> None:
        self.assertTrue(POSTGRES_URI, "CI must provide TEST_POSTGRES_URI")
        self._reset_database()
        self.app = bootstrap.build_app(SQLALCHEMY_DATABASE_URI=POSTGRES_URI)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self._reset_database()

    @staticmethod
    def _reset_database() -> None:
        engine = create_engine(POSTGRES_URI)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
