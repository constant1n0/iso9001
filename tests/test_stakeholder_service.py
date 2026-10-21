"""Interested parties service: the rules specific to ``ParteInteresada``.

OC-1 of ``organization-context`` (decision OC2) adds a type, relevance ("sin
evaluar" while unset), an owner, the next review date, never before ``today``
when a write sets it, and a climate-related-requirements flag.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import AuditLog, ParteInteresada, Person, TipoContexto

TODAY = date(2026, 10, 21)

VALID = {"nombre": "Clientes", "necesidades_expectativas": "Plazos", "requisitos_identificados": "ISO",
         "objetivo_estrategico": "Fidelizar"}


def service():
    from app.services import stakeholders

    return stakeholders


class StakeholderServiceTestCase(ServiceBase):
    def seed(self, **overrides) -> ParteInteresada:
        result = db.session.execute(ParteInteresada.__table__.insert().values(**(VALID | overrides)))
        db.session.commit()
        return db.session.get(ParteInteresada, result.inserted_primary_key[0])

    def test_name_is_required_and_limited_while_the_texts_are_optional(self) -> None:
        for data in ({"nombre": " "}, {"nombre": "x" * 51}, {}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), data)
                db.session.rollback()
        record = service().create(db.session, actor(), {"nombre": " Proveedores ", "objetivo_estrategico": ""})
        self.assertEqual(("Proveedores", None), (record.nombre, record.objetivo_estrategico))

    def test_a_duplicate_name_raises_conflict_with_a_spanish_message(self) -> None:
        self.seed()
        with self.assertRaises(errors().Conflict) as caught:
            service().create(db.session, actor(), VALID)
        self.assertEqual("Ya existe una parte interesada con ese nombre.", caught.exception.message)
        db.session.rollback()
        other = self.seed(nombre="Proveedores")
        with self.assertRaises(errors().Conflict) as caught:
            service().update(db.session, actor(), other.id_interesado, {"nombre": "Clientes"})
        self.assertEqual("Ya existe una parte interesada con ese nombre.", caught.exception.message)

    def test_list_is_ordered_by_name(self) -> None:
        b = self.seed(nombre="Bancos")
        a = self.seed(nombre="Aseguradoras")
        self.assertEqual([a.id_interesado, b.id_interesado], [x.id_interesado for x in service().list_(db.session, actor())])

    def test_unrelated_columns_are_not_writable_and_every_role_may_write(self) -> None:
        record = service().create(db.session, actor(), VALID)
        self.assertEqual(7, record.created_by_id)
        with self.assertRaises(errors().ValidationError):
            service().update(db.session, actor(), record.id_interesado, {"id_interesado": 5})


    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        result = db.session.execute(Person.__table__.insert().values(nombre=nombre,
                                                                      activo=activo))
        db.session.commit()
        return result.inserted_primary_key[0]

    def test_the_context_fields_start_unset_and_are_validated(self) -> None:
        bare = service().create(db.session, actor(), {"nombre": "Bancos"}, today=TODAY)
        self.assertEqual((None, None, None, None, False),
                         (bare.tipo, bare.pertinente, bare.responsable_id, bare.fecha_revision,
                          bare.requisitos_climaticos))
        ana, eva = self.person(), self.person("Eva Gil", activo=False)
        full = service().create(db.session, actor(), VALID | {
            "tipo": "externa", "pertinente": False, "responsable_id": ana,
            "fecha_revision": TODAY, "requisitos_climaticos": True}, today=TODAY)
        db.session.commit()
        self.assertEqual((TipoContexto.externa, False, ana, TODAY, True),
                         (full.tipo, full.pertinente, full.responsable_id, full.fecha_revision,
                          full.requisitos_climaticos))
        row = (db.session.query(AuditLog).filter_by(entity_type="partes_interesadas")
               .order_by(AuditLog.id.desc()).first())
        self.assertEqual(("Externa", False, True), (row.after["tipo"], row.after["pertinente"],
                                                    row.after["requisitos_climaticos"]))
        self.assertIsNone(service().update(db.session, actor(), full.id_interesado,
                                           {"pertinente": None}).pertinente)  # "sin evaluar"
        db.session.rollback()
        for data in ({"tipo": "mixta"}, {"tipo": "Externa"}, {"pertinente": "si"},
                     {"pertinente": 0}, {"requisitos_climaticos": None},
                     {"requisitos_climaticos": 1}, {"responsable_id": 999},
                     {"responsable_id": eva}, {"responsable_id": "1"},
                     {"fecha_revision": "2026-12-01"}):
            with self.subTest(data=data):
                with self.assertRaises(errors().ValidationError):
                    service().create(db.session, actor(), VALID | {"nombre": "Otra"} | data,
                                     today=TODAY)
                db.session.rollback()

    def test_a_review_date_set_by_a_write_is_never_before_today(self) -> None:
        yesterday = TODAY - timedelta(days=1)
        with self.assertRaises(errors().ValidationError) as caught:
            service().create(db.session, actor(), VALID | {"fecha_revision": yesterday},
                             today=TODAY)
        self.assertEqual(service().REVIEW_IN_PAST, caught.exception.message)
        db.session.rollback()
        overdue = self.seed(fecha_revision=date(2026, 5, 1))
        # An overdue date already stored stays while other fields change.
        changed = service().update(db.session, actor(), overdue.id_interesado, {
            "fecha_revision": date(2026, 5, 1), "objetivo_estrategico": "Crecer"}, today=TODAY)
        self.assertEqual("Crecer", changed.objetivo_estrategico)
        for value in (yesterday, date(2026, 5, 2)):
            with self.subTest(fecha_revision=value):
                with self.assertRaises(errors().ValidationError):
                    service().update(db.session, actor(), overdue.id_interesado,
                                     {"fecha_revision": value}, today=TODAY)
                db.session.rollback()
        self.assertEqual(TODAY, service().update(db.session, actor(), overdue.id_interesado,
                                                 {"fecha_revision": TODAY},
                                                 today=TODAY).fecha_revision)
        db.session.rollback()
        # The web screens do not send the date yet; a write that sets one needs ``today``.
        self.assertEqual("Sin fecha", service().update(
            db.session, actor(), overdue.id_interesado, {"objetivo_estrategico": "Sin fecha"}
        ).objetivo_estrategico)
        with self.assertRaises(ValueError):
            service().update(db.session, actor(), overdue.id_interesado,
                             {"fecha_revision": date(2027, 1, 1)})

    def test_revision_vencida_is_a_review_date_before_today(self) -> None:
        late, due, none = (self.seed(nombre=name, fecha_revision=value) for name, value in (
            ("Clientes", TODAY - timedelta(days=1)), ("Bancos", TODAY), ("Sociedad", None)))
        self.assertEqual([True, False, False], [service().revision_vencida(party, today=TODAY)
                                                for party in (late, due, none)])


if __name__ == "__main__":
    unittest.main()
