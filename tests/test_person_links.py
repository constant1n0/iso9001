"""Links from trainings, nonconformities and audits to people (QP-2 and QP-4, decision Q4).

Each register keeps its legacy free-text name and gains a nullable reference
to ``personas``: an unknown person is refused, an inactive one only when the
reference changes, and a person still cited cannot be deleted. When a write
leaves the legacy name blank, the linked person's name fills it (QP-4), on
every channel. The SQLite cases run with the audit flush guard
(``ServiceBase``). The foreign keys themselves are exercised on SQLite with
``PRAGMA foreign_keys=ON`` and on PostgreSQL when ``TEST_POSTGRES_URI`` is set
(CI provides it); so is the refusal to delete a role that a competence
requirement cites.
"""

from __future__ import annotations

import importlib
import os
import unittest
from datetime import date
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mcp_support import ADMIN as MCP_ADMIN, SEEDS, McpDbCase, mcp_actor
from register_routes import RegisterRoutesBase
from test_nonconformity_service import ServiceBase, errors

from app.extensions import db
from app.models import Auditoria, Capacitacion, NoConformidad, RoleEnum
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
ADMIN = Actor(user_id=None, label="cli", role=RoleEnum.ADMINISTRADOR, channel="cli")

# Service module, reference field, legacy text field and minimal valid data.
LINKS = (
    ("training", "persona_id", "personal",
     {"tema": "Seguridad", "fecha": date(2026, 10, 1), "personal": "Ana"}),
    ("nonconformities", "responsable_id", "responsable",
     {"descripcion": "Pieza fuera de tolerancia", "fecha_detectada": date(2026, 10, 1),
      "responsable": "Ana"}),
    ("audits", "auditor_id", "auditor",
     {"area_auditada": "Compras", "fecha": date(2026, 10, 1), "auditor": "Ana",
      "resultado": "Sin hallazgos"}),
)


def service(name: str):
    return importlib.import_module(f"app.services.{name}")


def person_model():
    from app.models import Person

    return Person


class LinkHelpers:
    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        """Core insert: these tests must not depend on the people write API."""
        table = person_model().__table__
        result = db.session.execute(table.insert().values(nombre=nombre, activo=activo))
        db.session.commit()
        return result.inserted_primary_key[0]

    def deactivate(self, person_id: int) -> None:
        table = person_model().__table__
        db.session.execute(table.update().where(table.c.id == person_id).values(activo=False))
        db.session.commit()

    def create(self, name: str, data: dict):
        record = service(name).create(db.session, ADMIN, data)
        db.session.commit()
        return record

    def update(self, name: str, record_id: int, data: dict):
        record = service(name).update(db.session, ADMIN, record_id, data)
        db.session.commit()
        return record

    def refused(self, call, *args):
        """The ``ValidationError`` message of a refused write; the session is rolled back."""
        with self.assertRaises(errors().ValidationError) as caught:
            call(db.session, ADMIN, *args)
        db.session.rollback()
        return caught.exception.message

    def role_with_requirement(self) -> tuple[int, int]:
        """A role and a competence requirement citing it, as ids (Core inserts)."""
        from app.models import CompetenceRequirement, CompetenceType, RolResponsabilidad

        rol = db.session.execute(
            RolResponsabilidad.__table__.insert().values(rol="Calidad")
        ).inserted_primary_key[0]
        requirement = db.session.execute(CompetenceRequirement.__table__.insert().values(
            rol_id=rol, tipo=CompetenceType.formacion, descripcion="Curso de auditor interno",
        )).inserted_primary_key[0]
        db.session.commit()
        return rol, requirement


def without(data: dict, key: str) -> dict:
    return {k: v for k, v in data.items() if k != key}


# Longest legacy text each register stores (``personal``, ``responsable``, ``auditor``).
TEXT_MAX = {"training": 100, "nonconformities": 50, "audits": 50}


class LinkTestCase(LinkHelpers, ServiceBase):
    def test_records_accept_return_and_audit_the_person(self) -> None:
        ana = self.person()
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                record = self.create(name, valid | {key: ana})
                self.assertEqual((ana, "Ana"), (getattr(record, key), getattr(record, legacy)))
                fetched = service(name).get(db.session, ADMIN, record.id)
                self.assertEqual(ana, getattr(fetched, key))
                self.assertEqual(ana, self.audit_rows()[-1].after[key])

    def test_the_person_can_be_changed_and_cleared(self) -> None:
        ana, eva = self.person(), self.person("Eva")
        for name, key, _legacy, valid in LINKS:
            with self.subTest(service=name):
                record_id = self.create(name, valid | {key: ana}).id
                self.assertEqual(eva, getattr(self.update(name, record_id, {key: eva}), key))
                row = self.audit_rows()[-1]
                self.assertEqual((ana, eva), (row.before[key], row.after[key]))
                self.assertIsNone(getattr(self.update(name, record_id, {key: None}), key))

    def test_an_unknown_or_malformed_person_is_refused(self) -> None:
        cases = (
            (999, "no corresponde a ninguna persona"),
            (0, "no corresponde a ninguna persona"),
            (2**40, "no corresponde a ninguna persona"),  # beyond a PostgreSQL integer
            ("1", "debe ser un número entero"),
            (True, "debe ser un número entero"),
        )
        for name, key, _legacy, valid in LINKS:
            record_id = self.create(name, valid).id
            for value, expected in cases:
                with self.subTest(service=name, value=value):
                    message = self.refused(service(name).create, valid | {key: value})
                    self.assertIn(expected, message)
                    self.assertIn(f"«{key}»", message)
                    message = self.refused(service(name).update, record_id, {key: value})
                    self.assertIn(expected, message)

    def test_an_inactive_person_is_refused_when_chosen(self) -> None:
        ana, luis = self.person(), self.person("Luis", activo=False)
        for name, key, _legacy, valid in LINKS:
            with self.subTest(service=name):
                message = self.refused(service(name).create, valid | {key: luis})
                self.assertIn("está desactivada", message)
                record_id = self.create(name, valid | {key: ana}).id
                message = self.refused(service(name).update, record_id, {key: luis})
                self.assertIn("está desactivada", message)

    def test_a_record_citing_a_person_later_deactivated_stays_editable(self) -> None:
        ana = self.person()
        records = {name: self.create(name, valid | {key: ana}).id
                   for name, key, _legacy, valid in LINKS}
        self.deactivate(ana)
        for name, key, legacy, _valid in LINKS:
            with self.subTest(service=name):
                updated = self.update(name, records[name], {legacy: "Ana P.", key: ana})
                self.assertEqual((ana, "Ana P."), (getattr(updated, key), getattr(updated, legacy)))
                updated = self.update(name, records[name], {legacy: "Ana Pérez"})
                self.assertEqual((ana, "Ana Pérez"),
                                 (getattr(updated, key), getattr(updated, legacy)))

    def test_the_legacy_text_is_kept_and_never_matched_to_a_person(self) -> None:
        ana = self.person("Ana")  # the same name the legacy text holds
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                record = self.create(name, valid)
                self.assertEqual(("Ana", None), (getattr(record, legacy), getattr(record, key)))
                updated = self.update(name, record.id, {key: ana})
                self.assertEqual(("Ana", ana), (getattr(updated, legacy), getattr(updated, key)))


class LegacyNameTestCase(LinkHelpers, ServiceBase):
    """QP-4: a linked person fills a blank legacy name, so lists, PDFs and reports keep it."""

    def test_a_person_fills_a_missing_or_blank_text_on_create(self) -> None:
        ana = self.person()
        for name, key, legacy, valid in LINKS:
            bare = without(valid, legacy)
            for data in (bare, bare | {legacy: ""}, bare | {legacy: "  "}, bare | {legacy: None}):
                with self.subTest(service=name, text=data.get(legacy, "absent")):
                    record = self.create(name, data | {key: ana})
                    self.assertEqual(("Ana Pérez", ana),
                                     (getattr(record, legacy), getattr(record, key)))
                    self.assertEqual("Ana Pérez", self.audit_rows()[-1].after[legacy])

    def test_an_explicit_text_beside_a_person_is_kept_as_typed(self) -> None:
        ana = self.person()
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                record = self.create(name, valid | {legacy: "A. Pérez (calidad)", key: ana})
                self.assertEqual("A. Pérez (calidad)", getattr(record, legacy))

    def test_without_a_person_the_text_stays_as_required_as_before(self) -> None:
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                if name == "nonconformities":  # «responsable» was always optional
                    record = self.create(name, without(valid, legacy) | {key: None})
                    self.assertEqual((None, None), (record.responsable, record.responsable_id))
                    continue
                self.assertIn("Faltan campos obligatorios",
                              self.refused(service(name).create, without(valid, legacy)))
                message = self.refused(service(name).create, valid | {legacy: " ", key: None})
                self.assertIn(f"«{legacy}» es obligatorio", message)

    def test_an_update_fills_a_cleared_text_from_the_linked_person(self) -> None:
        ana, eva = self.person(), self.person("Eva")
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                record_id = self.create(name, valid | {key: ana}).id
                self.assertEqual("Ana Pérez",
                                 getattr(self.update(name, record_id, {legacy: ""}), legacy))
                updated = self.update(name, record_id, {legacy: " ", key: eva})
                self.assertEqual(("Eva", eva), (getattr(updated, legacy), getattr(updated, key)))
                if name == "nonconformities":
                    updated = self.update(name, record_id, {legacy: "", key: None})
                    self.assertEqual((None, None), (updated.responsable, updated.responsable_id))
                    continue
                message = self.refused(service(name).update, record_id, {legacy: "", key: None})
                self.assertIn(f"«{legacy}» es obligatorio", message)

    def test_linking_a_person_fills_a_record_that_has_no_text(self) -> None:
        ana = self.person()
        _name, key, legacy, valid = LINKS[1]  # only «responsable» can be stored empty
        record_id = self.create("nonconformities", without(valid, legacy)).id
        updated = self.update("nonconformities", record_id, {key: ana})
        self.assertEqual(("Ana Pérez", ana), (getattr(updated, legacy), getattr(updated, key)))

    def test_a_person_deactivated_later_still_fills_a_cleared_text(self) -> None:
        ana = self.person()
        records = {name: self.create(name, valid | {key: ana}).id
                   for name, key, _legacy, valid in LINKS}
        self.deactivate(ana)
        for name, _key, legacy, _valid in LINKS:
            with self.subTest(service=name):
                self.assertEqual("Ana Pérez",
                                 getattr(self.update(name, records[name], {legacy: ""}), legacy))

    def test_a_long_name_is_cut_to_the_length_of_the_text(self) -> None:
        long_name = self.person("Nombre " + "x" * 113)  # 120 characters
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                record = self.create(name, without(valid, legacy) | {key: long_name})
                self.assertEqual(("Nombre " + "x" * 113)[:TEXT_MAX[name]], getattr(record, legacy))

    def test_an_unknown_person_without_text_is_reported_as_such(self) -> None:
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                message = self.refused(service(name).create, without(valid, legacy) | {key: 999})
                self.assertIn(f"«{key}» no corresponde a ninguna persona", message)

    def test_resending_an_inactive_person_with_a_blank_text_fills_the_name(self) -> None:
        ana = self.person()
        for name, key, legacy, valid in LINKS:
            with self.subTest(service=name):
                record_id = self.create(name, valid | {key: ana}).id
                self.deactivate(ana)
                record = self.update(name, record_id, {key: ana, legacy: ""})
                self.assertEqual((ana, "Ana Pérez"), (getattr(record, key), getattr(record, legacy)))
                db.session.execute(person_model().__table__.update().values(activo=True))
                db.session.commit()


class McpLegacyNameTestCase(McpDbCase):
    """The same rule reaches the MCP, which writes through the services."""

    async def test_qms_create_fills_the_text_from_the_person(self) -> None:
        person_id = self.seed("personas")  # «Ana Pérez»
        cases = (("capacitaciones", "persona_id", "personal"),
                 ("no_conformidades", "responsable_id", "responsable"),
                 ("auditorias", "auditor_id", "auditor"))
        for slug, key, legacy in cases:
            with self.subTest(module=slug):
                data = without(SEEDS[slug], legacy) | {key: person_id}
                created = await self.call(mcp_actor(MCP_ADMIN), "qms_create",
                                          {"module": slug, "data": data})
                self.assertFalse(created.is_error, created.content)
                self.assertEqual("Ana Pérez", created.structured_content[legacy])
                if slug == "no_conformidades":
                    continue
                refused = await self.call(mcp_actor(MCP_ADMIN), "qms_create",
                                          {"module": slug, "data": without(SEEDS[slug], legacy)})
                self.assertTrue(refused.is_error)
                self.assertIn("Faltan campos obligatorios", refused.content[0].text)


class PickerCases:
    """The person picker of one web form (QP-4); the concrete classes below set the names."""

    BASE = KEY = LEGACY = LABEL = NAME = ""
    MODEL: type = type(None)
    FORM: dict = {}  # a valid post without a person
    SEED: dict = {}  # valid service data without a person
    KEPT: tuple[str, str] = ("", "")  # another text field and a value to find after a refusal

    def setUp(self) -> None:
        super().setUp()
        self.login(RoleEnum.ADMINISTRADOR)

    def add_person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        with self.app.app_context():
            table = person_model().__table__
            result = db.session.execute(table.insert().values(nombre=nombre, activo=activo))
            db.session.commit()
            return result.inserted_primary_key[0]

    def deactivate(self, person_id: int) -> None:
        with self.app.app_context():
            table = person_model().__table__
            db.session.execute(table.update().where(table.c.id == person_id).values(activo=False))
            db.session.commit()

    def page(self, url: str) -> str:
        response = self.client.get(self.BASE + url)
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def test_the_form_offers_active_people_after_an_empty_choice(self) -> None:
        ana = self.add_person()
        self.add_person("Luis Gil", activo=False)
        html = self.page("/nueva")
        self.assertIn(f'<label for="{self.KEY}">{self.LABEL}</label>', html)
        self.assertRegex(html, rf'<select[^>]*name="{self.KEY}"[^>]*><option selected value="">')
        self.assertIn(f'<option value="{ana}">Ana Pérez</option>', html)
        self.assertNotIn("Luis Gil", html)

    def test_the_picked_person_reaches_the_service_and_fills_a_blank_text(self) -> None:
        ana = self.add_person()
        response = self.client.post(f"{self.BASE}/nueva",
                                    data=self.FORM | {self.LEGACY: "", self.KEY: str(ana)})
        self.assertEqual(302, response.status_code)
        with self.app.app_context():
            record = self.MODEL.query.one()
            self.assertEqual((ana, "Ana Pérez"),
                             (getattr(record, self.KEY), getattr(record, self.LEGACY)))

    def test_an_inactive_unknown_or_malformed_person_cannot_be_picked(self) -> None:
        luis = self.add_person("Luis Gil", activo=False)
        for value in (str(luis), "999", "x"):
            with self.subTest(value=value):
                response = self.client.post(f"{self.BASE}/nueva",
                                            data=self.FORM | {self.KEY: value})
                self.assertEqual(200, response.status_code)
        self.assertEqual(0, self.count(self.MODEL))

    def test_edit_keeps_the_linked_person_even_when_inactive(self) -> None:
        ana = self.add_person()
        record_id = self.seed_with(service(self.NAME), self.SEED | {self.KEY: ana})
        self.deactivate(ana)
        self.add_person("Luis Gil", activo=False)
        html = self.page(f"/editar/{record_id}")
        self.assertIn(f'<option selected value="{ana}">Ana Pérez (desactivada)</option>', html)
        self.assertNotIn("Luis Gil", html)
        response = self.client.post(f"{self.BASE}/editar/{record_id}",
                                    data=self.FORM | {self.KEY: str(ana), self.LEGACY: "Ana P."})
        self.assertEqual(302, response.status_code)
        record = self.fetch(self.MODEL, record_id)
        self.assertEqual((ana, "Ana P."), (getattr(record, self.KEY), getattr(record, self.LEGACY)))

    def test_without_a_person_a_blank_text_follows_the_service_rule(self) -> None:
        field, kept = self.KEPT
        response = self.client.post(f"{self.BASE}/nueva",
                                    data=self.FORM | {self.LEGACY: "", self.KEY: "", field: kept})
        if self.LEGACY == "responsable":  # optional, as it always was
            self.assertEqual(302, response.status_code)
            with self.app.app_context():
                self.assertIsNone(self.MODEL.query.one().responsable)
            return
        self.assertEqual(200, response.status_code)  # the form is shown again with the input
        html = response.get_data(as_text=True)
        self.assertIn(f"El campo «{self.LEGACY}» es obligatorio.", html)
        self.assertIn(kept, html)
        self.assertEqual(0, self.count(self.MODEL))

    def test_the_list_shows_the_linked_person(self) -> None:
        ana = self.add_person()
        self.seed_with(service(self.NAME), self.SEED | {self.LEGACY: "A. P.", self.KEY: ana})
        html = self.page("/")
        self.assertIn("A. P.", html)
        self.assertIn("Persona: Ana Pérez", html)


class TrainingPickerTestCase(PickerCases, RegisterRoutesBase):
    BASE, NAME, MODEL = "/capacitaciones", "training", Capacitacion
    KEY, LEGACY, LABEL = "persona_id", "personal", "Persona"
    FORM = {"tema": "Seguridad", "fecha": "2026-10-05", "personal": "Ana",
            "duracion_horas": "4", "evaluacion_final": ""}
    SEED = LINKS[0][3]
    KEPT = ("tema", "Tema conservado")


class NonconformityPickerTestCase(PickerCases, RegisterRoutesBase):
    BASE, NAME, MODEL = "/no_conformidades", "nonconformities", NoConformidad
    KEY, LEGACY, LABEL = "responsable_id", "responsable", "Responsable (persona)"
    FORM = {"descripcion": "Pieza fuera de tolerancia", "fecha_detectada": "2026-10-05",
            "responsable": "Ana", "estado": "Abierta", "accion_correctiva": ""}
    SEED = LINKS[1][3]
    KEPT = ("descripcion", "Descripción conservada")


class AuditPickerTestCase(PickerCases, RegisterRoutesBase):
    BASE, NAME, MODEL = "/auditorias", "audits", Auditoria
    KEY, LEGACY, LABEL = "auditor_id", "auditor", "Auditor (persona)"
    FORM = {"area_auditada": "Compras", "fecha": "2026-10-05", "auditor": "Luis",
            "resultado": "Sin hallazgos", "accion_correctiva": "", "estado": "PENDIENTE"}
    SEED = LINKS[2][3]
    KEPT = ("area_auditada", "Área conservada")


class DeleteCases:
    """Deleting a cited person; shared by the SQLite and PostgreSQL cases below."""

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_a_person_cited_by_any_record_cannot_be_deleted(self) -> None:
        from app.services import people

        for name, key, _legacy, valid in LINKS:
            with self.subTest(service=name):
                ana = self.person()
                record_id = self.create(name, valid | {key: ana}).id
                with self.assertRaises(errors().Conflict) as caught:
                    people.delete(db.session, ADMIN, ana)
                self.assertIn("desactívala en su lugar", caught.exception.message)
                db.session.rollback()
                self.assertIsNotNone(db.session.get(person_model(), ana))
                self.update(name, record_id, {key: None})
                people.delete(db.session, ADMIN, ana)
                db.session.commit()
                self.assertIsNone(db.session.get(person_model(), ana))

    def test_the_foreign_key_refuses_a_reference_the_check_did_not_see(self) -> None:
        from app.services import people

        self.enforce_foreign_keys()
        for name, key, _legacy, valid in LINKS:
            with self.subTest(service=name):
                ana = self.person()
                record_id = self.create(name, valid | {key: ana}).id
                # As if the reference were committed after the explicit check ran.
                with patch.object(people, "_is_referenced", return_value=False):
                    with self.assertRaises(errors().Conflict) as caught:
                        people.delete(db.session, ADMIN, ana)
                    self.assertIn("desactívala en su lugar", caught.exception.message)
                    self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                    db.session.rollback()
                    self.assertIsNotNone(db.session.get(person_model(), ana))
                    self.update(name, record_id, {key: None})  # control: no reference left
                    people.delete(db.session, ADMIN, ana)
                    db.session.commit()
                self.assertIsNone(db.session.get(person_model(), ana))

    def test_a_role_cited_by_a_competence_requirement_cannot_be_deleted(self) -> None:
        from app.models import RolResponsabilidad
        from app.services import competence, roles_responsibilities as roles

        rol, requirement = self.role_with_requirement()
        with self.assertRaises(errors().Conflict) as caught:
            roles.delete(db.session, ADMIN, rol)
        self.assertIn("requisitos de competencia", caught.exception.message)
        db.session.rollback()
        self.assertIsNotNone(db.session.get(RolResponsabilidad, rol))
        competence.requirements.delete(db.session, ADMIN, requirement)
        roles.delete(db.session, ADMIN, rol)  # control: nothing cites it any more
        db.session.commit()
        self.assertIsNone(db.session.get(RolResponsabilidad, rol))

    def test_the_role_foreign_key_refuses_a_requirement_the_check_did_not_see(self) -> None:
        from app.models import RolResponsabilidad
        from app.services import roles_responsibilities as roles

        self.enforce_foreign_keys()
        rol, _requirement = self.role_with_requirement()
        # As if the requirement were committed after the explicit check ran.
        with patch.object(roles, "_in_use", return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                roles.delete(db.session, ADMIN, rol)
        self.assertIn("requisitos de competencia", caught.exception.message)
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        db.session.rollback()
        self.assertIsNotNone(db.session.get(RolResponsabilidad, rol))

    def test_a_role_delete_with_a_malformed_id_ends_in_a_domain_error(self) -> None:
        from app.services import roles_responsibilities as roles

        for bad in ("abc", None, 0, -1, 2**40, True):
            with self.subTest(rol_id=bad):
                with self.assertRaises((errors().ValidationError, errors().NotFound)):
                    roles.delete(db.session, ADMIN, bad)
                db.session.rollback()


class SqliteDeleteTestCase(LinkHelpers, DeleteCases, ServiceBase):
    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())


# In CI a missing database must fail loudly instead of skipping silently.
@unittest.skipUnless(POSTGRES_URI or os.environ.get("CI"), "TEST_POSTGRES_URI is not set")
class PostgresDeleteTestCase(LinkHelpers, DeleteCases, unittest.TestCase):
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
