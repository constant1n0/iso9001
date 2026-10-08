"""People service: who works under the QMS and which roles they hold (QP-1).

Runs on the in-memory database with the audit flush guard installed (see
``ServiceBase``), so a write that forgets its audit row fails the test.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import RolResponsabilidad, RoleEnum, User

ADMIN, AUDITOR, OPERATIVO = RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR, RoleEnum.OPERATIVO


def people():
    from app.services import people as module

    return module


def person_model():
    from app.models import Person

    return Person


def admin(**kwargs):
    return actor(role=ADMIN, **kwargs)


class PeopleBase(ServiceBase):
    def role(self, name: str) -> int:
        """Core insert of a role (roles are audited; this bypasses the guard)."""
        result = db.session.execute(RolResponsabilidad.__table__.insert().values(rol=name))
        db.session.commit()
        return result.inserted_primary_key[0]

    def user(self, username: str = "ana.user") -> int:
        user = User(username=username, password="x", role=OPERATIVO)
        db.session.add(user)
        db.session.commit()
        return user.id

    def insert(self, **values) -> int:
        """Core insert: read tests must not depend on the write API."""
        table = person_model().__table__
        result = db.session.execute(table.insert().values(**({"nombre": "Ana"} | values)))
        db.session.commit()
        return result.inserted_primary_key[0]

    def create(self, who=None, **data):
        record = people().create(db.session, who or admin(), {"nombre": "Ana Pérez"} | data)
        db.session.commit()
        return record


class PolicyTestCase(PeopleBase):
    def test_every_role_reads_on_every_channel(self) -> None:
        record_id = self.insert()
        for role in RoleEnum:
            for channel in ("web", "mcp"):
                with self.subTest(role=role.name, channel=channel):
                    who = actor(role=role, channel=channel)
                    self.assertEqual(record_id, people().get(db.session, who, record_id).id)
                    self.assertEqual(1, people().list_page(db.session, who)[1])
                    self.assertEqual(1, len(people().list_(db.session, who)))

    def test_only_administrators_and_auditors_create_and_update(self) -> None:
        record_id = self.insert()
        for channel in ("web", "mcp"):
            with self.subTest(channel=channel):
                who = actor(role=OPERATIVO, channel=channel)
                with self.assertRaises(errors().PermissionDenied):
                    people().create(db.session, who, {"nombre": "Eva"})
                with self.assertRaises(errors().PermissionDenied):
                    people().update(db.session, who, record_id, {"nombre": "Eva"})
        db.session.rollback()
        self.assertEqual([], self.audit_rows())
        for role in (ADMIN, AUDITOR):
            for channel in ("web", "mcp"):
                who = actor(role=role, channel=channel)
                created = people().create(db.session, who, {"nombre": f"{role.name} {channel}"})
                people().update(db.session, who, created.id, {"notas": "x"})
        db.session.commit()
        self.assertEqual(8, len(self.audit_rows()))

    def test_only_administrators_delete_and_never_through_mcp(self) -> None:
        record_id = self.insert()
        for who in (actor(role=AUDITOR), actor(role=OPERATIVO), admin(channel="mcp")):
            with self.subTest(role=who.role.name, channel=who.channel):
                with self.assertRaises(errors().PermissionDenied):
                    people().delete(db.session, who, record_id)
        people().delete(db.session, admin(), record_id)
        db.session.commit()
        self.assertIsNone(db.session.get(person_model(), record_id))

    def test_a_read_only_token_cannot_write(self) -> None:
        with self.assertRaises(errors().PermissionDenied):
            people().create(
                db.session, admin(channel="mcp", scopes=("read",)), {"nombre": "Eva"}
            )


class CreateTestCase(PeopleBase):
    def test_creates_a_stamped_active_person_with_roles(self) -> None:
        quality, direction = self.role("Calidad"), self.role("Dirección")
        user_id = self.user()
        record = self.create(
            nombre="  Ana Pérez ", email=" Ana@Example.COM ", user_id=user_id,
            rol_ids=[direction, quality, quality], notas="Turno de mañana",
        )
        db.session.expire_all()
        stored = people().get(db.session, actor(), record.id)
        self.assertEqual(
            ("Ana Pérez", "ana@example.com", user_id, True, "Turno de mañana"),
            (stored.nombre, stored.email, stored.user_id, stored.activo, stored.notas),
        )
        self.assertEqual(sorted([quality, direction]), stored.rol_ids)
        self.assertEqual({"Calidad", "Dirección"}, {role.rol for role in stored.roles})
        self.assertEqual((7, 7), (stored.created_by_id, stored.updated_by_id))

    def test_optional_fields_default_to_empty_and_active(self) -> None:
        record = self.create()
        self.assertEqual(
            (None, None, True, None, []),
            (record.email, record.user_id, record.activo, record.notas, record.rol_ids),
        )
        blank = self.create(nombre="Eva", email="  ", notas="", rol_ids=None)
        self.assertEqual((None, None, []), (blank.email, blank.notas, blank.rol_ids))

    def test_invalid_values_are_rejected(self) -> None:
        cases = [
            {}, {"nombre": " "}, {"nombre": "x" * 151}, {"nombre": 3},
            {"nombre": "Ana", "email": "no-es-correo"},
            {"nombre": "Ana", "activo": "si"}, {"nombre": "Ana", "activo": None},
            {"nombre": "Ana", "user_id": "1"},
            {"nombre": "Ana", "rol_ids": "1"}, {"nombre": "Ana", "rol_ids": [True]},
            {"nombre": "Ana", "rol_ids": ["1"]},
            {"nombre": "Ana", "id": 5}, {"nombre": "Ana", "created_by_id": 1},
        ]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    people().create(db.session, admin(), data)
                db.session.rollback()
        self.assertEqual(150, len(self.create(nombre="x" * 150).nombre))

    def test_an_unknown_role_is_a_validation_error(self) -> None:
        known = self.role("Calidad")
        with self.assertRaises(errors().ValidationError) as caught:
            people().create(db.session, admin(), {"nombre": "Ana", "rol_ids": [999, known, 998]})
        self.assertEqual("Roles desconocidos: 998, 999.", caught.exception.message)
        db.session.rollback()
        self.assertEqual(0, db.session.query(person_model()).count())

    def test_an_unknown_user_is_a_validation_error(self) -> None:
        with self.assertRaises(errors().ValidationError) as caught:
            people().create(db.session, admin(), {"nombre": "Ana", "user_id": 999})
        self.assertEqual("El usuario indicado no existe.", caught.exception.message)

    def test_a_user_linked_to_another_person_is_a_conflict(self) -> None:
        user_id = self.user()
        first = self.create(user_id=user_id)
        other = self.create(nombre="Eva")
        with self.assertRaises(errors().Conflict) as caught:
            people().create(db.session, admin(), {"nombre": "Luis", "user_id": user_id})
        self.assertEqual("Ese usuario ya está vinculado a otra persona.", caught.exception.message)
        db.session.rollback()
        with self.assertRaises(errors().Conflict):
            people().update(db.session, admin(), other.id, {"user_id": user_id})
        db.session.rollback()
        # Keeping your own link is not a conflict, and unlinking frees the user.
        people().update(db.session, admin(), first.id, {"user_id": user_id})
        people().update(db.session, admin(), first.id, {"user_id": None})
        people().update(db.session, admin(), other.id, {"user_id": user_id})
        db.session.commit()
        self.assertEqual(user_id, db.session.get(person_model(), other.id).user_id)

    def test_emails_are_unique_ignoring_case_and_optional(self) -> None:
        self.create(email="ana@example.com")
        other = self.create(nombre="Eva", email="eva@example.com")
        with self.assertRaises(errors().Conflict) as caught:
            people().create(db.session, admin(), {"nombre": "Luis", "email": "ANA@example.com "})
        self.assertEqual(
            "Ya existe una persona con ese correo electrónico.", caught.exception.message
        )
        db.session.rollback()
        with self.assertRaises(errors().Conflict):
            people().update(db.session, admin(), other.id, {"email": "Ana@Example.com"})
        db.session.rollback()
        self.create(nombre="Sin correo 1")
        self.create(nombre="Sin correo 2")
        self.assertEqual(4, db.session.query(person_model()).count())


class UpdateTestCase(PeopleBase):
    def test_a_unique_violation_while_auditing_an_update_is_a_conflict(self) -> None:
        # A concurrent insert can win after the pre-check; the audit flush meets it.
        record = self.create(email="ana@example.com")
        race = IntegrityError("UPDATE personas", {}, Exception("unique"))
        with patch("app.services.people.audit.record", side_effect=race), \
                self.assertRaises(errors().Conflict):
            people().update(db.session, admin(), record.id, {"email": "otra@example.com"})
        db.session.rollback()

    def test_roles_are_replaced_and_the_change_is_audited(self) -> None:
        quality, direction, purchasing = (
            self.role("Calidad"), self.role("Dirección"), self.role("Compras")
        )
        record = self.create(rol_ids=[quality, direction])
        updated = people().update(
            db.session, admin(user_id=8), record.id, {"rol_ids": [purchasing, quality]}
        )
        db.session.commit()
        self.assertEqual(sorted([quality, purchasing]), updated.rol_ids)
        self.assertEqual(8, updated.updated_by_id)
        row = self.audit_rows()[-1]
        self.assertEqual(
            ("update", "personas", record.id), (row.action, row.entity_type, row.entity_id)
        )
        self.assertEqual(sorted([quality, direction]), row.before["rol_ids"])
        self.assertEqual(sorted([quality, purchasing]), row.after["rol_ids"])
        self.assertNotIn("nombre", row.after)

    def test_fields_change_and_a_person_is_deactivated(self) -> None:
        record = self.create(email="ana@example.com")
        updated = people().update(db.session, admin(), record.id, {
            "nombre": "Ana P. López", "activo": False, "email": None, "notas": "Baja",
        })
        db.session.commit()
        self.assertEqual(
            ("Ana P. López", False, None, "Baja"),
            (updated.nombre, updated.activo, updated.email, updated.notas),
        )
        row = self.audit_rows()[-1]
        self.assertEqual({"activo": True, "email": "ana@example.com"},
                         {k: row.before[k] for k in ("activo", "email")})
        self.assertNotIn("rol_ids", row.after)

    def test_an_update_that_changes_nothing_writes_nothing(self) -> None:
        quality, direction = self.role("Calidad"), self.role("Dirección")
        user_id = self.user()
        record = self.create(
            email="ana@example.com", user_id=user_id, rol_ids=[quality, direction], notas="n"
        )
        stamp, rows = record.updated_at, len(self.audit_rows())
        people().update(db.session, admin(user_id=8), record.id, {
            "nombre": " Ana Pérez ", "email": "ANA@example.com", "user_id": user_id,
            "rol_ids": [direction, quality, direction], "activo": True, "notas": "n",
        })
        people().update(db.session, admin(user_id=8), record.id, {})
        db.session.commit()
        self.assertEqual(rows, len(self.audit_rows()))
        db.session.expire_all()
        stored = db.session.get(person_model(), record.id)
        self.assertEqual((stamp, 7), (stored.updated_at, stored.updated_by_id))

    def test_unknown_ids_are_not_found(self) -> None:
        calls = (
            lambda: people().get(db.session, admin(), 999),
            lambda: people().update(db.session, admin(), 999, {"nombre": "x"}),
            lambda: people().delete(db.session, admin(), 999),
        )
        for call in calls:
            with self.assertRaises(errors().NotFound) as caught:
                call()
            self.assertEqual("Persona no encontrada.", caught.exception.message)


class ListTestCase(PeopleBase):
    def test_pages_follow_name_then_id_order(self) -> None:
        ids = [self.insert(nombre=name) for name in ("Carla", "Ana", "Bea", "Ana")]
        expected = [ids[1], ids[3], ids[2], ids[0]]
        self.assertEqual(expected, [p.id for p in people().list_(db.session, actor())])
        first, total = people().list_page(db.session, actor(), page=1, per_page=3)
        second, again = people().list_page(db.session, actor(), page=2, per_page=3)
        self.assertEqual((expected[:3], 4), ([p.id for p in first], total))
        self.assertEqual((expected[3:], 4), ([p.id for p in second], again))
        clamped, _ = people().list_page(db.session, actor(), page=0, per_page=0)
        self.assertEqual(expected, [p.id for p in clamped])

    def test_filters_narrow_the_rows_and_the_total(self) -> None:
        quality, direction = self.role("Calidad"), self.role("Dirección")
        ana = self.create(nombre="Ana Pérez", rol_ids=[quality, direction]).id
        eva = self.create(nombre="Eva Pérez", rol_ids=[quality]).id
        luis = self.create(nombre="Luis Gómez", activo=False).id

        def ids(**filters) -> list[int]:
            rows, total = people().list_page(db.session, actor(), **filters)
            self.assertEqual(len(rows), total, filters)
            self.assertEqual(rows, people().list_(db.session, actor(), **filters))
            return [p.id for p in rows]

        self.assertEqual([ana, eva], ids(nombre="pérez"))
        self.assertEqual([luis], ids(activo=False))
        self.assertEqual([ana, eva], ids(activo=True))
        self.assertEqual([ana, eva], ids(rol_id=quality))
        self.assertEqual([ana], ids(rol_id=direction))
        self.assertEqual([ana], ids(nombre="ana", rol_id=quality, activo=True))
        self.assertEqual([], ids(rol_id=999))
        self.assertEqual([ana, eva, luis], ids())


class DeleteTestCase(PeopleBase):
    def test_delete_removes_the_person_and_its_role_links_but_keeps_the_roles(self) -> None:
        from app.models import persona_roles

        quality = self.role("Calidad")
        record_id = self.create(rol_ids=[quality]).id
        people().delete(db.session, admin(), record_id)
        db.session.commit()
        self.assertIsNone(db.session.get(person_model(), record_id))
        links = db.session.scalar(select(func.count()).select_from(persona_roles))
        self.assertEqual(0, links)
        self.assertIsNotNone(db.session.get(RolResponsabilidad, quality))
        row = self.audit_rows()[-1]
        self.assertEqual(("delete", record_id, None), (row.action, row.entity_id, row.after))
        self.assertEqual(([quality], "Ana Pérez"), (row.before["rol_ids"], row.before["nombre"]))

    # Deleting a person still cited by a training, nonconformity or audit is
    # covered with real rows and foreign keys in ``test_person_links``.


class AuditTestCase(PeopleBase):
    def test_every_write_leaves_exactly_one_audit_row(self) -> None:
        quality = self.role("Calidad")
        record_id = self.create(rol_ids=[quality]).id
        people().update(db.session, admin(), record_id, {"notas": "x"})
        db.session.commit()
        people().delete(db.session, admin(), record_id)
        db.session.commit()
        rows = self.audit_rows()
        self.assertEqual(
            [(action, "personas", record_id) for action in ("create", "update", "delete")],
            [(r.action, r.entity_type, r.entity_id) for r in rows],
        )
        self.assertEqual({("web", 7)}, {(r.channel, r.actor_user_id) for r in rows})
        created = rows[0].after
        self.assertEqual(
            (record_id, [quality], True),
            (created["id"], created["rol_ids"], created["activo"]),
        )
        self.assertEqual("x", rows[1].after["notas"])
        self.assertNotIn("rol_ids", rows[1].after)


if __name__ == "__main__":
    unittest.main()
