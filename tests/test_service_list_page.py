"""Database paging (``list_page``) for the registers that used to page in memory.

Each register gets the same contract: pages follow the ``list_`` order with no
overlap or gap (rows sharing an order key included), the total counts every
matching row, filters narrow both the rows and the total, reading needs the read
permission and out-of-range bounds are clamped by ``crud.page_bounds``.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import (
    Capacitacion, Document, DocumentCategory, EstadoNoConformidad, NoConformidad,
    ParteInteresada, RoleEnum, SatisfaccionCliente,
)
from app.services import crud

ADMIN = RoleEnum.ADMINISTRADOR
OCT, SEP, AUG = date(2026, 10, 1), date(2026, 9, 1), date(2026, 8, 1)


def reader():
    """Administrators may read every register, documents included."""
    return actor(role=ADMIN)


class ListPageContract:
    """Mixed into a ``ServiceBase`` subclass; the subclass names its register."""

    model: type

    def service(self):
        raise NotImplementedError

    def seed_ordered(self) -> list[int]:
        """Insert five rows and return their ids in the expected list order."""
        raise NotImplementedError

    def insert(self, **values) -> int:
        # Core insert: the read tests must not depend on the write API, and core
        # statements bypass the audit flush guard.
        result = db.session.execute(self.model.__table__.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def ids(self, rows) -> list[int]:
        key = self.model.__mapper__.primary_key[0].key
        return [getattr(row, key) for row in rows]

    def page(self, page, per_page, who=None, **filters) -> tuple[list[int], int]:
        rows, total = self.service().list_page(
            db.session, who or reader(), **filters, page=page, per_page=per_page
        )
        return self.ids(rows), total

    def test_pages_follow_the_list_order_without_overlap_or_gap(self) -> None:
        expected = self.seed_ordered()
        self.assertEqual(expected, self.ids(self.service().list_(db.session, reader())))
        with patch.object(self.service(), "list_", side_effect=AssertionError("loaded every row")):
            pages = [self.page(number, 2) for number in (1, 2, 3, 4)]
        self.assertEqual([5, 5, 5, 5], [total for _ids, total in pages])
        self.assertEqual(expected[:2], pages[0][0])
        self.assertEqual(expected[2:4], pages[1][0])
        self.assertEqual(expected[4:], pages[2][0])
        self.assertEqual([], pages[3][0])

    def test_reading_needs_the_read_permission(self) -> None:
        self.seed_ordered()
        with self.assertRaises(errors().PermissionDenied):
            self.page(1, 2, who=actor(role=ADMIN, scopes={"write"}))

    def test_out_of_range_bounds_are_clamped(self) -> None:
        expected = self.seed_ordered()
        self.assertEqual((expected[:2], 5), self.page(0, 2))
        self.assertEqual((expected[:2], 5), self.page(-3, 2))
        self.assertEqual((expected, 5), self.page(1, 0))  # default size holds all five
        self.assertEqual((expected, 5), self.page(1, -1))
        self.assertEqual(([], 5), self.page(10**9, 2))  # clamped to MAX_PAGE, still empty
        with patch.object(crud, "MAX_PER_PAGE", 3):
            self.assertEqual((expected[:3], 5), self.page(1, 1000))


class FilteredListPageContract(ListPageContract):
    """Registers whose ``list_`` takes filters; ``list_page`` takes the same ones."""

    filters: dict
    expected_matches: list[int]  # set by ``seed_ordered``: the ids ``filters`` keeps, in order

    def test_filters_narrow_both_the_rows_and_the_total(self) -> None:
        self.seed_ordered()
        wanted = self.expected_matches
        self.assertGreaterEqual(len(wanted), 2)
        self.assertLess(len(wanted), 5)
        listed = self.service().list_(db.session, reader(), **self.filters)
        self.assertEqual(wanted, self.ids(listed))
        self.assertEqual((wanted[:1], len(wanted)), self.page(1, 1, **self.filters))
        self.assertEqual((wanted[1:2], len(wanted)), self.page(2, 1, **self.filters))
        self.assertEqual(wanted, self.page(1, 5, **self.filters)[0])


class NonconformityListPageTestCase(FilteredListPageContract, ServiceBase):
    model = NoConformidad
    filters = {"descripcion": "fuga", "estado": EstadoNoConformidad.abierta,
               "fecha_detectada": OCT}

    def service(self):
        from app.services import nonconformities

        return nonconformities

    def seed_ordered(self) -> list[int]:
        def nc(descripcion, fecha, estado=EstadoNoConformidad.abierta):
            return self.insert(descripcion=descripcion, fecha_detectada=fecha, estado=estado)

        old = nc("Ruido en linea", SEP)
        first = nc("Fuga de aceite", OCT)
        closed = nc("Fuga de agua", OCT, estado=EstadoNoConformidad.cerrada)
        oldest = nc("Fuga antigua", AUG)
        last = nc("Fuga menor", OCT)
        # Newest date first; rows sharing a date come newest id first.
        self.expected_matches = [last, first]
        return [last, closed, first, old, oldest]


class DocumentListPageTestCase(ListPageContract, ServiceBase):
    model = Document

    def service(self):
        from app.services import documents

        return documents

    def seed_ordered(self) -> list[int]:
        def doc(code):
            return self.insert(title=f"Doc {code}", code=code, version="1",
                               category=DocumentCategory.PROCEDIMIENTO_OPERATIVO,
                               issued_date=OCT, content="Texto")

        ids = {code: doc(code) for code in ("PR-02", "MC-01", "PR-10", "IT-05", "PR-01")}
        # ``code`` is unique, so the code order alone is total.
        return [ids[code] for code in ("IT-05", "MC-01", "PR-01", "PR-02", "PR-10")]

    def test_only_roles_allowed_to_read_documents_get_a_page(self) -> None:
        self.seed_ordered()
        with self.assertRaises(errors().PermissionDenied):
            self.page(1, 2, who=actor(role=RoleEnum.OPERATIVO))


class TrainingListPageTestCase(FilteredListPageContract, ServiceBase):
    model = Capacitacion
    filters = {"tema": "segur", "fecha": OCT, "personal": "ana"}

    def service(self):
        from app.services import training

        return training

    def seed_ordered(self) -> list[int]:
        def course(tema, fecha, personal):
            return self.insert(tema=tema, fecha=fecha, personal=personal)

        old = course("Seguridad", SEP, "Ana")
        first = course("Seguridad vial", OCT, "Ana Gil")
        other = course("Calidad", OCT, "Ana")
        oldest = course("Seguridad", AUG, "Luis")
        last = course("Seguridad", OCT, "ana")
        self.expected_matches = [last, first]
        return [last, other, first, old, oldest]


class SatisfactionListPageTestCase(FilteredListPageContract, ServiceBase):
    model = SatisfaccionCliente
    filters = {"cliente": "acme", "puntuacion_minima": 7}

    def service(self):
        from app.services import satisfaction

        return satisfaction

    def seed_ordered(self) -> list[int]:
        def survey(cliente, fecha, puntuacion):
            return self.insert(cliente=cliente, fecha_encuesta=fecha, puntuacion=puntuacion)

        old = survey("ACME", SEP, 9)
        first = survey("Acme Iberia", OCT, 7)
        other = survey("Beta", OCT, 10)
        low = survey("ACME", AUG, 3)
        last = survey("acme", OCT, 8)
        self.expected_matches = [last, first, old]
        return [last, other, first, old, low]


class StakeholderListPageTestCase(ListPageContract, ServiceBase):
    model = ParteInteresada

    def service(self):
        from app.services import stakeholders

        return stakeholders

    def seed_ordered(self) -> list[int]:
        names = ("Proveedores", "Clientes", "Empleados", "Accionistas", "Sociedad")
        ids = {name: self.insert(nombre=name) for name in names}
        # ``nombre`` is unique, so the name order alone is total.
        return [ids[name] for name in sorted(names)]


if __name__ == "__main__":
    unittest.main()
