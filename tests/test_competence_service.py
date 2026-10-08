"""Competence required per role and demonstrated per person (QP-3, decisions Q1 and Q5).

Runs on the in-memory database with the audit flush guard installed (see
``ServiceBase``), so a write that forgets its audit row fails the test. The
foreign keys themselves are exercised with ``PRAGMA foreign_keys=ON``; the
migration tests cover them on PostgreSQL.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import Capacitacion, RolResponsabilidad, RoleEnum
from app.services.actor import Actor

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
OBTAINED = date(2026, 3, 10)


def competence():
    from app.services import competence as module

    return module


def models():
    from app import models as module

    return module


class CompetenceBase(ServiceBase):
    def setUp(self) -> None:
        super().setUp()
        self.rol = self.insert(RolResponsabilidad, rol="Calidad")
        self.ana = self.insert(models().Person, nombre="Ana")

    def insert(self, model, **values) -> int:
        """Core insert: these tests must not depend on other write APIs."""
        result = db.session.execute(model.__table__.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def set_values(self, model, record_id: int, **values) -> None:
        """Core update, bypassing the services (e.g. to deactivate a person)."""
        table = model.__table__
        db.session.execute(table.update().where(table.c.id == record_id).values(**values))
        db.session.commit()

    def training(self) -> int:
        return self.insert(Capacitacion, tema="Seguridad", fecha=date(2026, 3, 1), personal="Ana")

    def requirement_data(self, **overrides) -> dict:
        return {"rol_id": self.rol, "tipo": "formacion",
                "descripcion": "Curso de prevención de riesgos"} | overrides

    def record_data(self, **overrides) -> dict:
        return {"persona_id": self.ana, "evidencia": "Certificado 123",
                "fecha_obtencion": OBTAINED} | overrides

    def create(self, register: str, data: dict, who=CLI):
        record = getattr(competence(), register).create(db.session, who, data)
        db.session.commit()
        return record

    def update(self, register: str, record_id: int, data: dict, who=CLI):
        record = getattr(competence(), register).update(db.session, who, record_id, data)
        db.session.commit()
        return record

    def refused(self, call, *args) -> str:
        """The ``ValidationError`` message of a refused write; nothing is left pending."""
        with self.assertRaises(errors().ValidationError) as caught:
            call(db.session, CLI, *args)
        self.assertEqual(([], []), (list(db.session.new), list(db.session.dirty)))
        db.session.rollback()
        return caught.exception.message

    def both(self) -> tuple[int, int]:
        """One requirement and one record citing it, as ids."""
        requirement = self.create("requirements", self.requirement_data())
        record = self.create("records", self.record_data(requisito_id=requirement.id))
        return requirement.id, record.id


class PolicyTestCase(CompetenceBase):
    def test_every_role_reads_on_every_channel(self) -> None:
        ids = dict(zip(("requirements", "records"), self.both()))
        for role in RoleEnum:
            for channel in ("web", "mcp"):
                who = actor(role=role, channel=channel)
                for register, record_id in ids.items():
                    with self.subTest(role=role.name, channel=channel, register=register):
                        service = getattr(competence(), register)
                        self.assertEqual(record_id, service.get(db.session, who, record_id).id)
                        self.assertEqual(1, len(service.list_(db.session, who)))
                        self.assertEqual(1, service.list_page(db.session, who)[1])

    def test_only_administrators_and_auditors_create_and_update(self) -> None:
        requirement_id, record_id = self.both()
        writes = (("requirements", requirement_id, self.requirement_data(), {"criterio": "x"}),
                  ("records", record_id, self.record_data(), {"evidencia": "Otro"}))
        for channel in ("web", "mcp"):
            who = actor(role=OPERATIVO, channel=channel)
            for register, target, data, change in writes:
                with self.subTest(channel=channel, register=register):
                    service = getattr(competence(), register)
                    with self.assertRaises(errors().PermissionDenied):
                        service.create(db.session, who, data)
                    with self.assertRaises(errors().PermissionDenied):
                        service.update(db.session, who, target, change)
        db.session.rollback()
        before = len(self.audit_rows())
        for role in (ADMIN, AUDITOR):
            for channel in ("web", "mcp"):
                who = actor(role=role, channel=channel)
                for register, _id, data, change in writes:
                    created = getattr(competence(), register).create(db.session, who, data)
                    getattr(competence(), register).update(db.session, who, created.id, change)
        db.session.commit()
        self.assertEqual(before + 16, len(self.audit_rows()))

    def test_the_policy_is_checked_before_the_data(self) -> None:
        bad = {"fecha_obtencion": OBTAINED, "fecha_caducidad": date(2026, 1, 1)}
        with self.assertRaises(errors().PermissionDenied):
            competence().records.create(db.session, actor(role=OPERATIVO), self.record_data(**bad))
        record_id = self.both()[1]
        with self.assertRaises(errors().PermissionDenied):
            competence().records.update(db.session, actor(role=OPERATIVO), record_id, bad)

    def test_only_administrators_delete_and_never_through_mcp(self) -> None:
        requirement_id, record_id = self.both()
        for who in (actor(role=AUDITOR), actor(role=OPERATIVO), actor(role=ADMIN, channel="mcp")):
            for register, target in (("records", record_id), ("requirements", requirement_id)):
                with self.subTest(role=who.role.name, channel=who.channel, register=register):
                    with self.assertRaises(errors().PermissionDenied):
                        getattr(competence(), register).delete(db.session, who, target)
        competence().records.delete(db.session, CLI, record_id)
        competence().requirements.delete(db.session, CLI, requirement_id)
        db.session.commit()
        self.assertEqual([None, None], [db.session.get(models().CompetenceRecord, record_id),
                                        db.session.get(models().CompetenceRequirement,
                                                       requirement_id)])


class RequirementTestCase(CompetenceBase):
    def test_create_get_update_and_audit(self) -> None:
        created = self.create("requirements", self.requirement_data(criterio="Certificado"))
        self.assertEqual((self.rol, models().CompetenceType.formacion, "Certificado"),
                         (created.rol_id, created.tipo, created.criterio))
        self.assertEqual("Formación", created.tipo.value)  # shown to users
        stored = db.session.execute(text("SELECT tipo FROM competencias_requeridas")).scalar_one()
        self.assertEqual("formacion", stored)  # stored as text
        got = competence().requirements.get(db.session, CLI, created.id)
        self.assertIs(created, got)
        self.update("requirements", created.id, {"tipo": "experiencia", "criterio": None})
        self.update("requirements", created.id, {"tipo": "experiencia"})  # no-op: no audit row
        rows = self.audit_rows()
        self.assertEqual([("competencias_requeridas", "create"), ("competencias_requeridas", "update")],
                         [(row.entity_type, row.action) for row in rows])
        self.assertEqual({"tipo": "Experiencia", "criterio": None},
                         {k: v for k, v in rows[-1].after.items() if k in ("tipo", "criterio")})
        competence().requirements.delete(db.session, CLI, created.id)
        db.session.commit()
        self.assertEqual("delete", self.audit_rows()[-1].action)

    def test_the_four_types_are_accepted_by_name_only(self) -> None:
        for name in ("educacion", "formacion", "habilidad", "experiencia"):
            with self.subTest(tipo=name):
                record = self.create("requirements", self.requirement_data(tipo=name))
                self.assertEqual(name, record.tipo.name)
        for value in ("Formación", "otro", None, 3):
            with self.subTest(tipo=value):
                message = self.refused(competence().requirements.create,
                                       self.requirement_data(tipo=value))
                self.assertEqual("El valor del campo «tipo» no es válido.", message)

    def test_invalid_fields_are_refused(self) -> None:
        create = competence().requirements.create
        cases = (
            (self.requirement_data(rol_id=999), "El campo «rol_id» no corresponde a ningún rol."),
            (self.requirement_data(rol_id=0), "El campo «rol_id» no corresponde a ningún rol."),
            (self.requirement_data(rol_id=2**40), "El campo «rol_id» no corresponde a ningún rol."),
            (self.requirement_data(rol_id=None), "El campo «rol_id» debe ser un número entero."),
            (self.requirement_data(descripcion=" "), "El campo «descripcion» es obligatorio."),
            (self.requirement_data(descripcion="x" * 501),
             "El campo «descripcion» admite como máximo 500 caracteres."),
            ({"rol_id": self.rol, "tipo": "formacion"}, "Faltan campos obligatorios: descripcion."),
            (self.requirement_data(id=1), "Campos no permitidos: id."),
        )
        for data, expected in cases:
            with self.subTest(expected=expected, data=data):
                self.assertEqual(expected, self.refused(create, data))
        record_id = self.create("requirements", self.requirement_data()).id
        self.assertEqual("El campo «rol_id» no corresponde a ningún rol.",
                         self.refused(competence().requirements.update, record_id, {"rol_id": 999}))

    def test_list_filters_by_role_and_type(self) -> None:
        other = self.insert(RolResponsabilidad, rol="Compras")
        for rol, tipo in ((self.rol, "formacion"), (self.rol, "habilidad"), (other, "habilidad")):
            self.create("requirements", self.requirement_data(rol_id=rol, tipo=tipo))
        service, kind = competence().requirements, models().CompetenceType
        self.assertEqual(2, len(service.list_(db.session, CLI, rol_id=self.rol)))
        self.assertEqual(2, len(service.list_(db.session, CLI, tipo=kind.habilidad)))
        self.assertEqual(1, service.list_page(db.session, CLI, rol_id=other,
                                              tipo=kind.habilidad)[1])
        self.assertEqual(0, service.list_page(db.session, CLI, rol_id=2**40)[1])

    def test_a_requirement_cited_by_a_record_cannot_be_deleted(self) -> None:
        requirement_id, record_id = self.both()
        with self.assertRaises(errors().Conflict) as caught:
            competence().requirements.delete(db.session, CLI, requirement_id)
        self.assertEqual(competence().REQUIREMENT_IN_USE, caught.exception.message)
        db.session.rollback()
        self.update("records", record_id, {"requisito_id": None})
        competence().requirements.delete(db.session, CLI, requirement_id)
        db.session.commit()
        self.assertIsNone(db.session.get(models().CompetenceRequirement, requirement_id))
        with self.assertRaises(errors().NotFound):
            competence().requirements.delete(db.session, CLI, requirement_id)


class RecordTestCase(CompetenceBase):
    def test_create_defaults_to_a_pending_evaluation_and_audits(self) -> None:
        created = self.create("records", self.record_data())
        pending = models().CompetenceEvaluation.pendiente
        self.assertEqual((self.ana, "Certificado 123", OBTAINED, pending, None, None),
                         (created.persona_id, created.evidencia, created.fecha_obtencion,
                          created.evaluacion_eficacia, created.fecha_caducidad,
                          created.requisito_id))
        stored = db.session.execute(
            text("SELECT evaluacion_eficacia FROM competencias_acreditadas")).scalar_one()
        self.assertEqual("pendiente", stored)
        row = self.audit_rows()[-1]
        self.assertEqual(("competencias_acreditadas", "create", "Pendiente"),
                         (row.entity_type, row.action, row.after["evaluacion_eficacia"]))
        self.assertIs(created, competence().records.get(db.session, CLI, created.id))

    def test_cites_a_requirement_a_training_and_an_evaluator(self) -> None:
        requirement_id = self.create("requirements", self.requirement_data()).id
        training_id, eva = self.training(), self.insert(models().Person, nombre="Eva")
        created = self.create("records", self.record_data(
            requisito_id=requirement_id, capacitacion_id=training_id,
            fecha_caducidad=date(2028, 3, 10), evaluacion_eficacia="eficaz",
            fecha_evaluacion=date(2026, 6, 1), evaluador_id=eva))
        self.assertEqual((requirement_id, training_id, eva, "Eficaz"),
                         (created.requisito_id, created.capacitacion_id, created.evaluador_id,
                          created.evaluacion_eficacia.value))
        updated = self.update("records", created.id, {"capacitacion_id": None,
                                                     "evaluacion_eficacia": "no_eficaz"})
        self.assertEqual((None, "no_eficaz"),
                         (updated.capacitacion_id, updated.evaluacion_eficacia.name))
        row = self.audit_rows()[-1]
        self.assertEqual(({"capacitacion_id": training_id, "evaluacion_eficacia": "Eficaz"},
                          {"capacitacion_id": None, "evaluacion_eficacia": "No eficaz"}),
                         ({k: v for k, v in row.before.items() if not k.startswith("updated")},
                          {k: v for k, v in row.after.items() if not k.startswith("updated")}))

    def test_unknown_references_are_refused(self) -> None:
        cases = (
            ("persona_id", "El campo «persona_id» no corresponde a ninguna persona."),
            ("evaluador_id", "El campo «evaluador_id» no corresponde a ninguna persona."),
            ("requisito_id",
             "El campo «requisito_id» no corresponde a ningún requisito de competencia."),
            ("capacitacion_id", "El campo «capacitacion_id» no corresponde a ninguna capacitación."),
        )
        record_id = self.create("records", self.record_data()).id
        for key, expected in cases:
            for value in (999, 0, 2**40):
                with self.subTest(key=key, value=value):
                    self.assertEqual(expected, self.refused(
                        competence().records.create, self.record_data(**{key: value})))
                    self.assertEqual(expected, self.refused(
                        competence().records.update, record_id, {key: value}))
        self.assertEqual("El campo «persona_id» debe ser un número entero.", self.refused(
            competence().records.create, self.record_data(persona_id=None)))

    def test_an_inactive_person_is_refused_only_when_chosen(self) -> None:
        luis = self.insert(models().Person, nombre="Luis", activo=False)
        evaluated = {"evaluacion_eficacia": "eficaz", "fecha_evaluacion": OBTAINED}
        for key, extra in (("persona_id", {}), ("evaluador_id", evaluated)):
            with self.subTest(key=key):
                message = self.refused(competence().records.create,
                                       self.record_data(**extra, **{key: luis}))
                self.assertIn(f"La persona del campo «{key}» está desactivada", message)
        record = self.create("records", self.record_data(evaluador_id=self.ana, **evaluated))
        self.set_values(models().Person, self.ana, activo=False)
        updated = self.update("records", record.id, {"persona_id": self.ana,
                                                    "evaluador_id": self.ana,
                                                    "evidencia": "Certificado 124"})
        self.assertEqual("Certificado 124", updated.evidencia)

    def test_expiry_cannot_precede_the_obtained_date(self) -> None:
        expected = "La fecha de caducidad no puede ser anterior a la fecha de obtención."
        early = date(2026, 3, 9)
        self.assertEqual(expected, self.refused(
            competence().records.create, self.record_data(fecha_caducidad=early)))
        record = self.create("records", self.record_data(fecha_caducidad=OBTAINED))
        self.assertEqual(expected, self.refused(
            competence().records.update, record.id, {"fecha_caducidad": early}))
        self.assertEqual(expected, self.refused(
            competence().records.update, record.id, {"fecha_obtencion": date(2026, 3, 11)}))
        self.assertIsNone(self.update("records", record.id, {"fecha_caducidad": None})
                          .fecha_caducidad)

    def test_an_evaluation_needs_its_date_and_evaluator(self) -> None:
        expected = ("Una evaluación de eficacia distinta de «pendiente» necesita "
                    "la fecha de evaluación y el evaluador.")
        complete = {"evaluacion_eficacia": "eficaz", "fecha_evaluacion": OBTAINED,
                    "evaluador_id": self.ana}
        for missing in ("fecha_evaluacion", "evaluador_id"):
            with self.subTest(missing=missing):
                partial_data = {k: v for k, v in complete.items() if k != missing}
                self.assertEqual(expected, self.refused(
                    competence().records.create, self.record_data(**partial_data)))
        record = self.create("records", self.record_data())
        self.assertEqual(expected, self.refused(
            competence().records.update, record.id, {"evaluacion_eficacia": "no_eficaz"}))
        self.update("records", record.id, complete)
        self.assertEqual(expected, self.refused(
            competence().records.update, record.id, {"evaluador_id": None}))
        back = self.update("records", record.id, {"evaluacion_eficacia": "pendiente",
                                                  "fecha_evaluacion": None, "evaluador_id": None})
        self.assertEqual("pendiente", back.evaluacion_eficacia.name)

    def test_a_datetime_is_refused_for_the_optional_dates(self) -> None:
        moment = datetime(2026, 3, 10, 9, 30)  # a ``datetime`` is also a ``date``
        record_id = self.create("records", self.record_data()).id
        for key in ("fecha_caducidad", "fecha_evaluacion"):
            with self.subTest(key=key):
                expected = f"El campo «{key}» debe ser una fecha."
                self.assertEqual(expected, self.refused(
                    competence().records.create, self.record_data(**{key: moment})))
                self.assertEqual(expected, self.refused(
                    competence().records.update, record_id, {key: moment}))

    def test_one_update_keeps_an_unchanged_reference_and_refuses_a_changed_unknown_one(
            self) -> None:
        record_id = self.create("records", self.record_data()).id
        # The person was deactivated later: citing it again is fine, choosing it anew is not.
        self.set_values(models().Person, self.ana, activo=False)
        self.assertEqual(
            "El campo «capacitacion_id» no corresponde a ninguna capacitación.",
            self.refused(competence().records.update, record_id,
                         {"persona_id": self.ana, "capacitacion_id": 999}))
        training_id = self.training()
        updated = self.update("records", record_id,
                              {"persona_id": self.ana, "capacitacion_id": training_id})
        self.assertEqual((self.ana, training_id), (updated.persona_id, updated.capacitacion_id))

    def test_the_evaluation_cannot_precede_the_obtained_date(self) -> None:
        expected = "La fecha de evaluación no puede ser anterior a la fecha de obtención."
        evaluated = {"evaluacion_eficacia": "eficaz", "fecha_evaluacion": date(2026, 3, 9),
                     "evaluador_id": self.ana}
        self.assertEqual(expected, self.refused(
            competence().records.create, self.record_data(**evaluated)))
        record = self.create("records", self.record_data(
            **(evaluated | {"fecha_evaluacion": OBTAINED})))
        self.assertEqual(expected, self.refused(
            competence().records.update, record.id, {"fecha_obtencion": date(2026, 3, 11)}))

    def test_invalid_fields_are_refused(self) -> None:
        create = competence().records.create
        cases = (
            (self.record_data(fecha_obtencion="2026-03-10"),
             "El campo «fecha_obtencion» es obligatorio y debe ser una fecha."),
            (self.record_data(fecha_caducidad="pronto"), "El campo «fecha_caducidad» debe ser una fecha."),
            (self.record_data(fecha_evaluacion=1), "El campo «fecha_evaluacion» debe ser una fecha."),
            (self.record_data(evaluacion_eficacia="No eficaz"),
             "El valor del campo «evaluacion_eficacia» no es válido."),
            (self.record_data(evidencia=""), "El campo «evidencia» es obligatorio."),
            (self.record_data(evidencia="x" * 501),
             "El campo «evidencia» admite como máximo 500 caracteres."),
            ({"persona_id": self.ana, "evidencia": "x"},
             "Faltan campos obligatorios: fecha_obtencion."),
            (self.record_data(nota="x"), "Campos no permitidos: nota."),
            ("texto", "Los datos deben ser un objeto con campos."),
        )
        for data, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, self.refused(create, data))

    def test_list_filters_by_person_requirement_and_evaluation(self) -> None:
        requirement_id = self.create("requirements", self.requirement_data()).id
        eva = self.insert(models().Person, nombre="Eva")
        evaluated = {"evaluacion_eficacia": "eficaz", "fecha_evaluacion": date(2026, 4, 2),
                     "evaluador_id": eva}
        self.create("records", self.record_data(requisito_id=requirement_id))
        self.create("records", self.record_data(fecha_obtencion=date(2026, 4, 1), **evaluated))
        self.create("records", self.record_data(persona_id=eva, requisito_id=requirement_id))
        service, evaluation = competence().records, models().CompetenceEvaluation
        newest = service.list_(db.session, CLI, persona_id=self.ana)
        self.assertEqual([date(2026, 4, 1), OBTAINED], [r.fecha_obtencion for r in newest])
        self.assertEqual(2, len(service.list_(db.session, CLI, requisito_id=requirement_id)))
        self.assertEqual(1, service.list_page(db.session, CLI,
                                              evaluacion_eficacia=evaluation.eficaz)[1])
        self.assertEqual(1, service.list_page(db.session, CLI, persona_id=self.ana,
                                              evaluacion_eficacia=evaluation.pendiente)[1])
        self.assertEqual(0, service.list_page(db.session, CLI, persona_id=2**40)[1])


class DeleteProtectionTestCase(CompetenceBase):
    def enforce_foreign_keys(self) -> None:
        db.session.commit()
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        db.session.commit()
        self.assertEqual(1, db.session.execute(text("PRAGMA foreign_keys")).scalar_one())

    def test_a_person_cited_by_a_competence_record_cannot_be_deleted(self) -> None:
        from app.services import people

        eva = self.insert(models().Person, nombre="Eva")
        record = self.create("records", self.record_data(
            evaluacion_eficacia="eficaz", fecha_evaluacion=OBTAINED, evaluador_id=eva))
        for person in (self.ana, eva):
            with self.subTest(person=person):
                with self.assertRaises(errors().Conflict) as caught:
                    people.delete(db.session, CLI, person)
                self.assertEqual(people.STILL_REFERENCED, caught.exception.message)
                db.session.rollback()
        competence().records.delete(db.session, CLI, record.id)
        people.delete(db.session, CLI, eva)
        db.session.commit()
        self.assertIsNone(db.session.get(models().Person, eva))

    def test_the_foreign_keys_refuse_what_the_explicit_checks_did_not_see(self) -> None:
        from app.services import people

        self.enforce_foreign_keys()
        requirement_id, record_id = self.both()
        with patch.object(people, "_is_referenced", return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                people.delete(db.session, CLI, self.ana)
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        db.session.rollback()
        with patch.object(competence(), "_requirement_in_use", return_value=False):
            with self.assertRaises(errors().Conflict) as caught:
                competence().requirements.delete(db.session, CLI, requirement_id)
        self.assertEqual(competence().REQUIREMENT_IN_USE, caught.exception.message)
        self.assertIsInstance(caught.exception.__cause__, IntegrityError)
        db.session.rollback()
        self.assertIsNotNone(db.session.get(models().CompetenceRequirement, requirement_id))

    def test_deleting_a_cited_training_clears_the_link(self) -> None:
        from app.services import training

        self.enforce_foreign_keys()
        training_id = self.training()
        record_id = self.create("records", self.record_data(capacitacion_id=training_id)).id
        training.delete(db.session, CLI, training_id)
        db.session.commit()
        db.session.expire_all()
        self.assertIsNone(db.session.get(models().CompetenceRecord, record_id).capacitacion_id)
