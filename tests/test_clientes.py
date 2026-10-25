"""Customer master data, policy, attribution and delete protection."""

from datetime import date
from pathlib import Path
import sys

# Support both unittest discovery and explicit tests.test_* module loading.
if __package__ == 'tests':
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from test_nonconformity_service import ServiceBase, actor, errors
from app.extensions import db
from app.models import AuditLog, Cliente, Reclamacion, RoleEnum, SatisfaccionCliente
from app.services import clientes


class CustomerTestCase(ServiceBase):
    def customer(self, **values):
        found = clientes.create(db.session, actor(RoleEnum.ADMINISTRADOR),
                                {"nombre": "Farmacia Sol"} | values)
        db.session.commit()
        return found

    def test_create_normalizes_contact_and_stamps_and_audits(self):
        found = self.customer(nombre="  Farmacia Sol  ", nif="b-1234 5678",
                              email="  SOL@EXAMPLE.COM ", contacto=" Ana ", notas="Notas")
        self.assertEqual(("Farmacia Sol", "B12345678", "sol@example.com", "Ana"),
                         (found.nombre, found.nif, found.email, found.contacto))
        self.assertEqual(7, found.created_by_id)
        self.assertEqual("clientes", self.audit_rows()[-1].entity_type)
        self.assertEqual("B12345678", self.audit_rows()[-1].after["nif"])

    def test_names_and_normalized_tax_ids_are_unique_but_blank_tax_ids_are_not(self):
        self.customer(nif="B12345678")
        for values in ({"nombre": "Farmacia Sol"},
                       {"nombre": "Otra", "nif": "b-12345678"}):
            with self.assertRaises(errors().Conflict):
                self.customer(**values)
            db.session.rollback()
        self.customer(nombre="Sin NIF", nif=" - ")
        self.customer(nombre="Otra sin NIF", nif=None)
        self.assertEqual(3, Cliente.query.count())

    def test_field_validation_and_whitelist(self):
        for values in ({"nombre": " "}, {"nombre": "x" * 201}, {"nif": "ABC"},
                       {"nif": "B1234567890"}, {"nif": 12345678}, {"nif": "Ñ12345678"},
                       {"email": "bad"}, {"contacto": "x" * 151},
                       {"telefono": "x" * 31}, {"estado": "activo"}, {"created_by_id": 1}):
            with self.subTest(values=values), self.assertRaises(errors().ValidationError):
                self.customer(**values)
            db.session.rollback()

    def test_policy_and_noop_update(self):
        found = self.customer()
        for role in RoleEnum:
            self.assertIs(found, clientes.get(db.session, actor(role), found.id))
        with self.assertRaises(errors().PermissionDenied):
            clientes.create(db.session, actor(), {"nombre": "Denied"})
        clientes.update(db.session, actor(RoleEnum.AUDITOR), found.id, {"nombre": found.nombre})
        db.session.commit()
        self.assertEqual(1, AuditLog.query.count())
        with self.assertRaises(errors().PermissionDenied):
            clientes.delete(db.session, actor(RoleEnum.AUDITOR), found.id)
        with self.assertRaises(errors().PermissionDenied):
            clientes.delete(db.session, actor(RoleEnum.ADMINISTRADOR, channel="mcp"), found.id)

    def test_order_name_filter_and_pagination(self):
        later = self.customer(nombre="Zeta")
        first = self.customer(nombre="Alfa")
        self.assertEqual(([first], 2), clientes.list_page(db.session, actor(), per_page=1))
        self.assertEqual([later], clientes.list_(db.session, actor(), nombre="ETA"))

    def test_delete_protects_surveys_and_complaints(self):
        for model, values, message in (
            (SatisfaccionCliente, {"fecha_encuesta": date(2026, 1, 1), "puntuacion": 8},
             clientes.HAS_SURVEYS),
            (Reclamacion, {"fecha_recepcion": date(2026, 1, 1), "descripcion": "Retraso"},
             clientes.HAS_COMPLAINTS),
        ):
            found = self.customer(nombre=model.__tablename__)
            db.session.execute(model.__table__.insert().values(cliente_id=found.id, **values))
            db.session.commit()
            with self.assertRaises(errors().Conflict) as caught:
                clientes.delete(db.session, actor(RoleEnum.ADMINISTRADOR), found.id)
            self.assertEqual(message, caught.exception.message)
            db.session.rollback()
        free = self.customer(nombre="Libre")
        clientes.delete(db.session, actor(RoleEnum.ADMINISTRADOR), free.id)
        db.session.commit()
        self.assertEqual("delete", self.audit_rows()[-1].action)

    def test_schema_names_lengths_and_uniqueness(self):
        self.assertEqual(200, Cliente.__table__.c.nombre.type.length)
        self.assertEqual({"uq_clientes_nombre", "uq_clientes_nif"},
                         {c.name for c in Cliente.__table__.constraints if c.name and
                          c.name.startswith("uq_")})
        self.customer()
        with self.assertRaises(IntegrityError):
            db.session.execute(text("INSERT INTO clientes (nombre) VALUES ('Farmacia Sol')"))
        db.session.rollback()
