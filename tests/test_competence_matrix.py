"""Competence matrix (QP-5): required versus demonstrated competence (ISO 9001 clause 7.2).

``competence.matrix`` lists, for every role with competence requirements, the
active people holding that role and one status per requirement, decided by
the records of that person for that requirement against ``today``:

- ``cumplida``: a record that has not expired (no expiry date, or one on or
  after today) and was evaluated ``eficaz``;
- ``pendiente_evaluacion`` / ``no_eficaz``: no such record, and the newest
  unexpired record is ``pendiente`` / ``no_eficaz``;
- ``caducada``: every record has expired;
- ``falta``: no record.

The cell keeps the record that decided it: the newest unexpired ``eficaz``
one, else the newest unexpired one, else the newest one ("newest" by date
obtained, then id). Runs on the in-memory database (see ``CompetenceBase``).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import event

from test_competence_service import CompetenceBase
from test_nonconformity_service import actor, errors

from app.extensions import db
from app.models import (
    CompetenceEvaluation, CompetenceRecord, CompetenceRequirement, CompetenceType, Person,
    RoleEnum, RolResponsabilidad, persona_roles,
)

TODAY = date(2026, 10, 9)
YESTERDAY = TODAY - timedelta(days=1)
EFICAZ, PENDIENTE, NO_EFICAZ = (CompetenceEvaluation.eficaz, CompetenceEvaluation.pendiente,
                                CompetenceEvaluation.no_eficaz)


def competence():
    from app.services import competence as module

    return module


class MatrixBase(CompetenceBase):
    """``self.rol`` is «Calidad» and ``self.ana`` holds it."""

    def setUp(self) -> None:
        super().setUp()
        self.hold(self.ana, self.rol)

    def hold(self, person_id: int, *role_ids: int) -> None:
        for role_id in role_ids:
            db.session.execute(persona_roles.insert().values(persona_id=person_id,
                                                             rol_id=role_id))
        db.session.commit()

    def role(self, rol: str) -> int:
        return self.insert(RolResponsabilidad, rol=rol)

    def person(self, nombre: str, *role_ids: int, activo: bool = True) -> int:
        person_id = self.insert(Person, nombre=nombre, activo=activo)
        self.hold(person_id, *role_ids)
        return person_id

    def requirement(self, descripcion: str, rol_id: int | None = None) -> int:
        return self.insert(CompetenceRequirement, rol_id=rol_id or self.rol,
                           tipo=CompetenceType.formacion, descripcion=descripcion)

    def record(self, requirement: int, evaluation=EFICAZ, *, person: int | None = None,
               obtained: date = date(2026, 1, 10), expires: date | None = None) -> int:
        return self.insert(CompetenceRecord, persona_id=person or self.ana,
                           requisito_id=requirement, evidencia="Certificado",
                           fecha_obtencion=obtained, fecha_caducidad=expires,
                           evaluacion_eficacia=evaluation)

    def matrix(self, today: date = TODAY, who=None, **filters):
        return competence().matrix(db.session, who or actor(RoleEnum.OPERATIVO),
                                   today=today, **filters)

    def cells(self, today: date = TODAY, **filters) -> dict:
        """``(role, person, requirement) -> (status name, record id)`` for every cell."""
        return {
            (group.role.rol, row.person.nombre, cell.requirement.descripcion):
                (cell.status.name, cell.record.id if cell.record is not None else None)
            for group in self.matrix(today, **filters)
            for row in group.rows
            for cell in row.cells
        }


class StatusTestCase(MatrixBase):
    def test_each_status_with_its_spanish_label(self) -> None:
        met = self.requirement("Cumplida")
        pending = self.requirement("Pendiente")
        failed = self.requirement("No eficaz")
        expired = self.requirement("Caducada")
        self.requirement("Falta")
        ids = {
            "Cumplida": self.record(met),
            "Pendiente": self.record(pending, PENDIENTE, expires=TODAY),  # valid through today
            "No eficaz": self.record(failed, NO_EFICAZ),
            "Caducada": self.record(expired, expires=YESTERDAY),
        }
        self.assertEqual({
            ("Calidad", "Ana", "Cumplida"): ("cumplida", ids["Cumplida"]),
            ("Calidad", "Ana", "Pendiente"): ("pendiente_evaluacion", ids["Pendiente"]),
            ("Calidad", "Ana", "No eficaz"): ("no_eficaz", ids["No eficaz"]),
            ("Calidad", "Ana", "Caducada"): ("caducada", ids["Caducada"]),
            ("Calidad", "Ana", "Falta"): ("falta", None),
        }, self.cells())
        self.assertEqual(
            ["Cumplida", "Pendiente de evaluación", "No eficaz", "Caducada", "Falta"],
            [status.value for status in competence().MatrixStatus])

    def test_expiry_is_judged_against_the_given_day(self) -> None:
        requirement = self.requirement("Carné de carretillero")
        self.record(requirement, expires=TODAY)
        key = ("Calidad", "Ana", "Carné de carretillero")
        for today, expected in ((YESTERDAY, "cumplida"), (TODAY, "cumplida"),
                                (TODAY + timedelta(days=1), "caducada")):
            with self.subTest(today=today):
                self.assertEqual(expected, self.cells(today)[key][0])

    def test_records_without_a_requirement_or_of_someone_else_do_not_count(self) -> None:
        requirement = self.requirement("Auditor interno")
        luis = self.person("Luis", self.rol)
        self.record(requirement, person=luis)
        self.insert(CompetenceRecord, persona_id=self.ana, evidencia="Libre",
                    fecha_obtencion=date(2026, 1, 10), evaluacion_eficacia=EFICAZ)
        cells = self.cells()
        self.assertEqual("falta", cells[("Calidad", "Ana", "Auditor interno")][0])
        self.assertEqual("cumplida", cells[("Calidad", "Luis", "Auditor interno")][0])


class BestRecordTestCase(MatrixBase):
    """Which record decides a cell when a person has several for one requirement."""

    def scenario(self, *records: tuple) -> tuple[str, int]:
        """Records as ``(evaluation, obtained, expires)``; returns the cell and the record ids."""
        requirement = self.requirement("Requisito")
        ids = [self.record(requirement, evaluation, obtained=obtained, expires=expires)
               for evaluation, obtained, expires in records]
        ((status, record),) = self.cells().values()
        return status, ids.index(record)

    def test_rules(self) -> None:
        jan, feb, mar = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
        cases = (
            # An unexpired «eficaz» record wins over anything newer.
            ("eficaz then a newer no_eficaz", ((EFICAZ, jan, None), (NO_EFICAZ, feb, None)),
             ("cumplida", 0)),
            ("eficaz then a newer pendiente", ((EFICAZ, jan, None), (PENDIENTE, feb, None)),
             ("cumplida", 0)),
            ("two eficaz: the newest", ((EFICAZ, feb, None), (EFICAZ, jan, None)),
             ("cumplida", 0)),
            # Otherwise the newest unexpired record decides.
            ("expired eficaz then pendiente", ((EFICAZ, jan, YESTERDAY), (PENDIENTE, feb, None)),
             ("pendiente_evaluacion", 1)),
            ("no_eficaz then a newer pendiente", ((NO_EFICAZ, jan, None),
                                                  (PENDIENTE, feb, None)),
             ("pendiente_evaluacion", 1)),
            ("pendiente then a newer no_eficaz", ((PENDIENTE, jan, None),
                                                  (NO_EFICAZ, feb, None)),
             ("no_eficaz", 1)),
            ("a newer expired no_eficaz is ignored", ((PENDIENTE, jan, None),
                                                      (NO_EFICAZ, feb, YESTERDAY)),
             ("pendiente_evaluacion", 0)),
            # Only expired records: the newest.
            ("only expired", ((EFICAZ, jan, YESTERDAY), (NO_EFICAZ, mar, YESTERDAY),
                              (EFICAZ, feb, YESTERDAY)),
             ("caducada", 1)),
        )
        for name, records, expected in cases:
            with self.subTest(name):
                try:
                    self.assertEqual(expected, self.scenario(*records))
                finally:
                    db.session.execute(CompetenceRecord.__table__.delete())
                    db.session.execute(CompetenceRequirement.__table__.delete())
                    db.session.commit()

    def test_same_day_records_are_ordered_by_id(self) -> None:
        requirement = self.requirement("Requisito")
        self.record(requirement, PENDIENTE)
        newest = self.record(requirement, NO_EFICAZ)
        self.assertEqual({("Calidad", "Ana", "Requisito"): ("no_eficaz", newest)}, self.cells())


class ShapeTestCase(MatrixBase):
    def test_roles_people_and_requirements_in_order(self) -> None:
        almacen = self.role("almacén")  # sorts before «Calidad» only when case is ignored
        self.role("Sin requisitos")
        second = self.requirement("Segundo")
        first_almacen = self.requirement("Inventario", almacen)
        self.requirement("Primero")
        self.person("Beatriz", self.rol, almacen)
        self.person("Abel", almacen)
        groups = self.matrix()
        self.assertEqual(["almacén", "Calidad"], [g.role.rol for g in groups])
        self.assertEqual([first_almacen], [r.id for r in groups[0].requirements])
        self.assertEqual(["Segundo", "Primero"], [r.descripcion for r in groups[1].requirements])
        self.assertEqual(second, groups[1].requirements[0].id)
        self.assertEqual(["Abel", "Beatriz"], [row.person.nombre for row in groups[0].rows])
        self.assertEqual(["Ana", "Beatriz"], [row.person.nombre for row in groups[1].rows])
        for group in groups:
            for row in group.rows:
                self.assertEqual(group.requirements, [cell.requirement for cell in row.cells])

    def test_only_active_holders_are_rows(self) -> None:
        self.requirement("Auditor interno")
        self.person("Eva", self.rol, activo=False)
        self.person("Luis")  # holds no role
        (group,) = self.matrix()
        self.assertEqual(["Ana"], [row.person.nombre for row in group.rows])

    def test_a_role_nobody_holds_keeps_its_columns(self) -> None:
        compras = self.role("Compras")
        self.requirement("Negociación", compras)
        groups = {g.role.rol: g for g in self.matrix()}
        self.assertEqual([], groups["Compras"].rows)
        self.assertEqual(["Negociación"], [r.descripcion for r in groups["Compras"].requirements])

    def test_no_requirement_means_no_role(self) -> None:
        self.assertEqual([], self.matrix())

    def test_the_role_filter(self) -> None:
        compras = self.role("Compras")
        self.requirement("Auditor interno")
        self.requirement("Negociación", compras)
        self.person("Luis", self.rol, compras)
        self.assertEqual(["Calidad", "Compras"], [g.role.rol for g in self.matrix()])
        for rol_id, expected in ((compras, ["Compras"]), (self.rol, ["Calidad"]), (999, []),
                                 (2**40, []), (0, [])):
            with self.subTest(rol_id=rol_id):
                self.assertEqual(expected, [g.role.rol for g in self.matrix(rol_id=rol_id)])
        (group,) = self.matrix(rol_id=compras)
        self.assertEqual(["Luis"], [row.person.nombre for row in group.rows])


class AccessTestCase(MatrixBase):
    def test_every_role_reads_it(self) -> None:
        self.requirement("Auditor interno")
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.assertEqual(1, len(self.matrix(who=actor(role))))

    def test_a_token_without_the_read_scope_is_refused(self) -> None:
        with self.assertRaises(errors().PermissionDenied):
            self.matrix(who=actor(RoleEnum.ADMINISTRADOR, channel="mcp", scopes=("write",)))


class QueryCountTestCase(MatrixBase):
    """The number of statements does not grow with people, requirements or records."""

    def statements(self) -> int:
        executed: list[str] = []

        def count(conn, cursor, statement, *args) -> None:
            executed.append(statement)

        db.session.expunge_all()
        event.listen(db.engine, "before_cursor_execute", count)
        try:
            self.matrix()
        finally:
            event.remove(db.engine, "before_cursor_execute", count)
        return len(executed)

    def test_bounded_queries(self) -> None:
        requirement = self.requirement("Auditor interno")
        self.record(requirement)
        small = self.statements()
        compras = self.role("Compras")
        for n in range(4):
            extra = self.requirement(f"Requisito {n}", compras)
            person = self.person(f"Persona {n}", self.rol, compras)
            self.record(extra, person=person)
            self.record(requirement, PENDIENTE, person=person)
        self.assertEqual(small, self.statements())
        self.assertLessEqual(small, 6)
