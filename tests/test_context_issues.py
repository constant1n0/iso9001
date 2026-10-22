"""Context issues of the organization (OC-1 of ``organization-context``).

ISO 9001 clause 4.1, decisions OC1, OC4 and OC5. An issue is internal or
external, with a category that matches its type, an effect, whether it is
relevant (a justification when it is not), whether it concerns climate change,
an owner, its identification date (never in the future), its next review (never
before the identification) and its state (``vigente`` or ``superada``). Risks
and opportunities may name the issue that gives rise to them, which then cannot
be deleted; neither can a person who owns an issue or an interested party.
``attention`` gathers the overdue reviews the dashboard flags, and ``changes``
the issues new, changed and superseded in a management review's period. The
SQLite cases run with the audit flush guard; the database cases also run on
PostgreSQL, which names the foreign key a delete breaks.
"""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from mcp_support import ADMIN, AUDITOR, OPERATIVO, McpDbCase, mcp_actor
from test_nonconformity_service import ServiceBase, actor, errors

from app.extensions import db
from app.models import (
    AuditLog, CategoriaCuestion, CuestionContexto, EfectoCuestion, EstadoCuestion,
    Oportunidad, ParteInteresada, Person, Riesgo, TipoContexto,
)
from app.services.actor import Actor

POSTGRES_URI = os.environ.get("TEST_POSTGRES_URI")
TODAY = date(2026, 10, 21)
YESTERDAY = TODAY - timedelta(days=1)
# No user id: attribution and audit rows stay valid when SQLite enforces foreign keys.
CLI = Actor(user_id=None, label="cli", role=ADMIN, channel="cli")
ISSUE = {"tipo": "externa", "categoria": "legal", "titulo": "Nueva normativa de envases",
         "descripcion": "El reglamento europeo de envases obliga a rediseñar el embalaje.",
         "efecto": "negativo", "pertinente": True, "fecha_identificacion": date(2026, 9, 1)}
RISK = {"titulo": "Sanción por envases", "descripcion": "Embalaje no conforme",
        "origen": "externo"}
OPPORTUNITY = {"titulo": "Envase reutilizable", "descripcion": "Diferenciarse",
               "origen": "externo"}


def ci():
    from app.services import context_issues

    return context_issues


def people():
    from app.services import people as module

    return module


def risks():
    from app.services import risks as module

    return module


class Helpers:
    def insert(self, table, **values) -> int:
        """Core insert of a record that is not under test."""
        result = db.session.execute(table.insert().values(**values))
        db.session.commit()
        return result.inserted_primary_key[0]

    def person(self, nombre: str = "Ana Pérez", activo: bool = True) -> int:
        return self.insert(Person.__table__, nombre=nombre, activo=activo)

    def party(self, nombre: str = "Clientes", **values) -> int:
        return self.insert(ParteInteresada.__table__, nombre=nombre, **values)

    def issue(self, who: Actor = CLI, **overrides) -> CuestionContexto:
        created = ci().issues.create(db.session, who, ISSUE | overrides, today=TODAY)
        db.session.commit()
        return created

    def edit(self, issue_id: int, data: dict, who: Actor = CLI) -> CuestionContexto:
        found = ci().issues.update(db.session, who, issue_id, data, today=TODAY)
        db.session.commit()
        return found

    def risk(self, cuestion_id: int, owner: int) -> Riesgo:
        found = risks().risks.create(db.session, CLI, RISK | {
            "cuestion_id": cuestion_id, "responsable_id": owner}, today=TODAY)
        db.session.commit()
        return found

    def opportunity(self, cuestion_id: int, owner: int) -> Oportunidad:
        found = risks().opportunities.create(db.session, CLI, OPPORTUNITY | {
            "cuestion_id": cuestion_id, "responsable_id": owner}, today=TODAY)
        db.session.commit()
        return found

    def rows(self, entity_type: str) -> list[AuditLog]:
        return (db.session.query(AuditLog).filter_by(entity_type=entity_type)
                .order_by(AuditLog.id).all())

    def refused(self, error, call, *args, **kwargs) -> str:
        """Run ``call``, expect ``error`` and roll back; returns the message."""
        with self.assertRaises(error) as caught:
            call(db.session, *args, **kwargs)
        db.session.rollback()
        return caught.exception.message

    @staticmethod
    def ids(records) -> list[int]:
        return [record.id for record in records]


class ChoicesTestCase(unittest.TestCase):
    def test_categories_belong_to_one_type_and_show_their_spanish_labels(self) -> None:
        from app.models import CATEGORIAS_POR_TIPO

        self.assertEqual(
            (["politica", "economica", "social", "tecnologica", "legal", "ambiental"],
             ["cultura", "recursos", "conocimiento", "desempeno", "estructura"]),
            tuple([member.name for member in CATEGORIAS_POR_TIPO[tipo]]
                  for tipo in (TipoContexto.externa, TipoContexto.interna)))
        self.assertEqual(list(CategoriaCuestion),
                         [*CATEGORIAS_POR_TIPO[TipoContexto.externa],
                          *CATEGORIAS_POR_TIPO[TipoContexto.interna]])
        self.assertEqual(["Interna", "Externa"], [member.value for member in TipoContexto])
        self.assertEqual(["Positivo", "Negativo", "Neutro"],
                         [member.value for member in EfectoCuestion])
        self.assertEqual(["Vigente", "Superada"], [member.value for member in EstadoCuestion])
        self.assertEqual(("Económica", "Desempeño", "Tecnológica"),
                         (CategoriaCuestion.economica.value, CategoriaCuestion.desempeno.value,
                          CategoriaCuestion.tecnologica.value))


class IssueWriteTestCase(Helpers, ServiceBase):
    def test_create_stores_every_field_with_defaults_stamps_and_audit(self) -> None:
        ana = self.person()
        created = ci().issues.create(db.session, actor(AUDITOR), ISSUE | {
            "titulo": "  Nueva normativa de envases ", "responsable_id": ana,
            "fecha_revision": date(2027, 3, 1), "notas": "Seguir el BOE"}, today=TODAY)
        db.session.commit()
        self.assertEqual(
            (TipoContexto.externa, CategoriaCuestion.legal, EfectoCuestion.negativo, True, None,
             False, ana, date(2026, 9, 1), date(2027, 3, 1), EstadoCuestion.vigente,
             "Nueva normativa de envases", 7),
            (created.tipo, created.categoria, created.efecto, created.pertinente,
             created.justificacion_pertinencia, created.cambio_climatico, created.responsable_id,
             created.fecha_identificacion, created.fecha_revision, created.estado,
             created.titulo, created.created_by_id))
        (row,) = self.rows("cuestiones_contexto")
        self.assertEqual(("create", "Externa", "Legal", "Vigente", False),
                         (row.action, row.after["tipo"], row.after["categoria"],
                          row.after["estado"], row.after["cambio_climatico"]))
        self.assertIs(created, ci().issues.get(db.session, actor(OPERATIVO), created.id))

    def test_required_fields_and_bad_values_are_refused(self) -> None:
        for missing in ("tipo", "categoria", "titulo", "descripcion", "efecto", "pertinente",
                        "fecha_identificacion"):
            with self.subTest(missing=missing):
                data = {key: value for key, value in ISSUE.items() if key != missing}
                message = self.refused(errors().ValidationError, ci().issues.create, CLI, data,
                                       today=TODAY)
                self.assertIn(missing, message)
        for data in ({"tipo": "mixta"}, {"tipo": "Externa"}, {"categoria": "Legal"},
                     {"efecto": "malo"}, {"estado": "cerrada"}, {"pertinente": None},
                     {"pertinente": "si"}, {"pertinente": 1}, {"cambio_climatico": None},
                     {"cambio_climatico": "no"}, {"titulo": " "}, {"titulo": "x" * 201},
                     {"descripcion": ""}, {"fecha_identificacion": "2026-09-01"},
                     {"fecha_revision": "2027-01-01"}, {"responsable_id": "1"},
                     {"id": 5}, {"created_at": None}):
            with self.subTest(data=data):
                self.refused(errors().ValidationError, ci().issues.create, CLI, ISSUE | data,
                             today=TODAY)
        self.assertEqual(0, db.session.query(CuestionContexto).count())

    def test_the_category_must_match_the_type(self) -> None:
        for tipo, categorias in (("externa", ("politica", "economica", "social", "tecnologica",
                                              "legal", "ambiental")),
                                 ("interna", ("cultura", "recursos", "conocimiento",
                                              "desempeno", "estructura"))):
            for categoria in categorias:
                with self.subTest(tipo=tipo, categoria=categoria):
                    created = ci().issues.create(db.session, CLI, ISSUE | {
                        "tipo": tipo, "categoria": categoria}, today=TODAY)
                    self.assertEqual(categoria, created.categoria.name)
                    db.session.rollback()
        for tipo, categoria in (("externa", "cultura"), ("interna", "legal"),
                                ("interna", "ambiental"), ("externa", "desempeno")):
            with self.subTest(refused=(tipo, categoria)):
                message = self.refused(errors().ValidationError, ci().issues.create, CLI,
                                       ISSUE | {"tipo": tipo, "categoria": categoria},
                                       today=TODAY)
                self.assertEqual(ci().CATEGORY_MISMATCH, message)
        issue = self.issue()
        for data in ({"tipo": "interna"}, {"categoria": "cultura"}):
            with self.subTest(update=data):
                self.assertEqual(ci().CATEGORY_MISMATCH, self.refused(
                    errors().ValidationError, ci().issues.update, CLI, issue.id, data,
                    today=TODAY))
        moved = self.edit(issue.id, {"tipo": "interna", "categoria": "recursos"})
        self.assertEqual((TipoContexto.interna, CategoriaCuestion.recursos),
                         (moved.tipo, moved.categoria))

    def test_an_issue_that_is_not_relevant_says_why(self) -> None:
        for data in ({"pertinente": False}, {"pertinente": False,
                                             "justificacion_pertinencia": "   "}):
            with self.subTest(data=data):
                self.assertEqual(ci().JUSTIFICATION_REQUIRED, self.refused(
                    errors().ValidationError, ci().issues.create, CLI, ISSUE | data,
                    today=TODAY))
        kept = self.issue(pertinente=False, justificacion_pertinencia="No afecta al SGC")
        self.assertEqual((False, "No afecta al SGC"),
                         (kept.pertinente, kept.justificacion_pertinencia))
        self.assertEqual(ci().JUSTIFICATION_REQUIRED, self.refused(
            errors().ValidationError, ci().issues.update, CLI, kept.id,
            {"justificacion_pertinencia": ""}, today=TODAY))
        relevant = self.issue(titulo="Otra")
        self.assertEqual(ci().JUSTIFICATION_REQUIRED, self.refused(
            errors().ValidationError, ci().issues.update, CLI, relevant.id,
            {"pertinente": False}, today=TODAY))
        # A relevant issue may keep a justification of its relevance.
        self.assertEqual("Afecta a ventas", self.edit(relevant.id, {
            "justificacion_pertinencia": "Afecta a ventas"}).justificacion_pertinencia)
        self.assertTrue(self.edit(kept.id, {"pertinente": True,
                                            "justificacion_pertinencia": None}).pertinente)

    def test_identification_is_never_in_the_future_and_the_review_never_before_it(self) -> None:
        self.assertEqual(ci().IDENTIFIED_IN_FUTURE, self.refused(
            errors().ValidationError, ci().issues.create, CLI,
            ISSUE | {"fecha_identificacion": TODAY + timedelta(days=1)}, today=TODAY))
        self.assertEqual(ci().REVIEW_BEFORE_IDENTIFICATION, self.refused(
            errors().ValidationError, ci().issues.create, CLI,
            ISSUE | {"fecha_revision": date(2026, 8, 31)}, today=TODAY))
        issue = self.issue(fecha_identificacion=TODAY, fecha_revision=TODAY)
        self.assertEqual(ci().REVIEW_BEFORE_IDENTIFICATION, self.refused(
            errors().ValidationError, ci().issues.update, CLI, issue.id,
            {"fecha_identificacion": date(2026, 10, 1), "fecha_revision": date(2026, 9, 30)},
            today=TODAY))
        self.assertEqual(ci().REVIEW_BEFORE_IDENTIFICATION, self.refused(
            errors().ValidationError, ci().issues.update, CLI, issue.id,
            {"fecha_revision": YESTERDAY}, today=TODAY))
        # A past review stays editable: overdue reviews are what the dashboard flags.
        older = self.issue(titulo="Antigua", fecha_identificacion=date(2026, 1, 10),
                           fecha_revision=date(2026, 6, 30))
        self.assertEqual("Cambio", self.edit(older.id, {"notas": "Cambio"}).notas)
        with self.assertRaises(ValueError):
            ci().issues.create(db.session, CLI, ISSUE, today=datetime(2026, 10, 21, 9, 0))

    def test_the_owner_is_a_person_active_when_newly_chosen(self) -> None:
        ana, eva = self.person(), self.person("Eva Gil", activo=False)
        for value, word in ((999, "ninguna persona"), (eva, "desactivada")):
            with self.subTest(responsable_id=value):
                self.assertIn(word, self.refused(errors().ValidationError, ci().issues.create,
                                                 CLI, ISSUE | {"responsable_id": value},
                                                 today=TODAY))
        issue = self.issue(responsable_id=ana)
        db.session.execute(Person.__table__.update().where(Person.id == ana)
                           .values(activo=False))
        db.session.commit()
        self.assertEqual("Sigue", self.edit(issue.id, {"notas": "Sigue",
                                                       "responsable_id": ana}).notas)
        self.assertIsNone(self.edit(issue.id, {"responsable_id": None}).responsable_id)

    def test_updates_audit_only_changed_fields_and_a_no_op_writes_nothing(self) -> None:
        issue = self.issue()
        same = ci().issues.update(db.session, CLI, issue.id, {
            "titulo": " Nueva normativa de envases ", "tipo": "externa", "categoria": "legal",
            "pertinente": True, "notas": ""}, today=TODAY)
        db.session.commit()
        self.assertIs(issue, same)
        self.assertEqual(1, len(self.rows("cuestiones_contexto")))
        self.edit(issue.id, {"estado": "superada", "cambio_climatico": True})
        row = self.rows("cuestiones_contexto")[-1]
        changed = {key for key in row.after if key not in ("updated_at", "updated_by_id")}
        self.assertEqual(("update", {"estado", "cambio_climatico"}, "Superada"),
                         (row.action, changed, row.after["estado"]))

    def test_unknown_issues_are_not_found(self) -> None:
        self.assertEqual(ci().ISSUE_NOT_FOUND,
                         self.refused(errors().NotFound, ci().issues.get, CLI, 999))
        for record_id in (999, 2 ** 40, "7", None):
            with self.subTest(record_id=record_id):
                for call, args, kwargs in (
                        (ci().issues.update, (record_id, {"notas": "x"}), {"today": TODAY}),
                        (ci().issues.delete, (record_id,), {})):
                    self.assertEqual(ci().ISSUE_NOT_FOUND, self.refused(
                        errors().NotFound, call, CLI, *args, **kwargs))


class FilterTestCase(Helpers, ServiceBase):
    def test_issues_filter_by_every_column_and_overdue_review(self) -> None:
        ana = self.person()
        legal = self.issue(responsable_id=ana, fecha_revision=YESTERDAY).id
        climate = self.issue(titulo="Olas de calor", categoria="ambiental", efecto="negativo",
                             cambio_climatico=True, fecha_revision=TODAY).id
        culture = self.issue(tipo="interna", categoria="cultura", titulo="Cultura de calidad",
                             efecto="positivo", pertinente=False,
                             justificacion_pertinencia="Ya consolidada").id
        gone = self.issue(titulo="Arancel retirado", categoria="economica", estado="superada",
                          fecha_revision=date(2026, 9, 2)).id
        who = actor(OPERATIVO)
        for filters, expected in (
            ({}, [climate, gone, legal, culture]),  # by type, category, title
            ({"tipo": "interna"}, [culture]),
            ({"tipo": TipoContexto.externa}, [climate, gone, legal]),
            ({"categoria": "legal"}, [legal]),
            ({"pertinente": False}, [culture]),
            ({"pertinente": True}, [climate, gone, legal]),
            ({"cambio_climatico": True}, [climate]),
            ({"estado": "superada"}, [gone]),
            ({"responsable_id": ana}, [legal]),
            ({"responsable_id": 2 ** 40}, []),
            ({"revision_vencida": True, "today": TODAY}, [legal]),  # not the superseded one
            ({"revision_vencida": False, "today": TODAY}, [climate, gone, culture]),
            ({"tipo": "externa", "estado": "vigente", "cambio_climatico": False}, [legal]),
        ):
            with self.subTest(filters=filters):
                self.assertEqual(expected, self.ids(ci().issues.list_(db.session, who,
                                                                       **filters)))
        page, total = ci().issues.list_page(db.session, who, page=2, per_page=3)
        self.assertEqual(([culture], 4), (self.ids(page), total))
        for filters in ({"tipo": "mixta"}, {"categoria": "Legal"}, {"estado": "cerrada"},
                        {"pertinente": "si"}, {"cambio_climatico": 1},
                        {"revision_vencida": "si", "today": TODAY}):
            with self.subTest(refused=filters):
                self.refused(errors().ValidationError, ci().issues.list_, who, **filters)
        with self.assertRaises(ValueError):
            ci().issues.list_(db.session, who, revision_vencida=True)

    def test_an_overdue_review_is_one_of_a_current_issue_before_today(self) -> None:
        late = self.issue(fecha_revision=YESTERDAY)
        due = self.issue(titulo="Hoy", fecha_revision=TODAY)
        none = self.issue(titulo="Sin revisión")
        gone = self.issue(titulo="Superada", estado="superada", fecha_revision=YESTERDAY)
        self.assertEqual([True, False, False, False],
                         [ci().revision_vencida(found, today=TODAY)
                          for found in (late, due, none, gone)])


class LinkTestCase(Helpers, ServiceBase):
    def test_linked_returns_the_risks_and_opportunities_an_issue_gives_rise_to(self) -> None:
        ana = self.person()
        issue, other = self.issue(), self.issue(titulo="Otra")
        first, second = self.risk(issue.id, ana), self.risk(issue.id, ana)
        self.risk(other.id, ana)
        opportunity = self.opportunity(issue.id, ana)
        found = ci().linked(db.session, actor(OPERATIVO), issue.id)
        self.assertEqual(({second.id, first.id}, [opportunity.id]),
                         (set(self.ids(found["riesgos"])), self.ids(found["oportunidades"])))
        self.assertEqual({"riesgos": sorted([first.id, second.id]),
                          "oportunidades": [opportunity.id]},
                         ci().linked_ids(db.session, issue.id))
        self.assertEqual({"riesgos": [], "oportunidades": []},
                         ci().linked_ids(db.session, self.issue(titulo="Sola").id))
        self.assertEqual(ci().ISSUE_NOT_FOUND, self.refused(
            errors().NotFound, ci().linked, actor(OPERATIVO), 999))
        self.refused(errors().PermissionDenied, ci().linked,
                     actor(ADMIN, scopes={"write"}), issue.id)

    def test_an_issue_cited_by_risks_or_opportunities_is_not_deleted(self) -> None:
        ana = self.person()
        by_risk, by_opportunity, free = (self.issue(titulo=name)
                                         for name in ("Riesgo", "Oportunidad", "Libre"))
        risk = self.risk(by_risk.id, ana)
        self.opportunity(by_opportunity.id, ana)
        for found in (by_risk, by_opportunity):
            with self.subTest(issue=found.titulo):
                self.assertEqual(ci().CITED_BY_RISKS, self.refused(
                    errors().Conflict, ci().issues.delete, actor(ADMIN), found.id))
        ci().issues.delete(db.session, actor(ADMIN), free.id)
        db.session.commit()
        self.assertEqual(("delete", "Libre"), (self.rows("cuestiones_contexto")[-1].action,
                                               self.rows("cuestiones_contexto")[-1]
                                               .before["titulo"]))
        risks().risks.update(db.session, CLI, risk.id, {"cuestion_id": None}, today=TODAY)
        ci().issues.delete(db.session, CLI, by_risk.id)
        db.session.commit()
        self.assertEqual(1, db.session.query(CuestionContexto).count())

    def test_people_owning_an_issue_or_an_interested_party_are_not_deleted(self) -> None:
        ana, eva, free = (self.person(name) for name in ("Ana", "Eva", "Libre"))
        self.issue(responsable_id=ana)
        self.party(responsable_id=eva)
        for person_id in (ana, eva):
            with self.subTest(person=person_id):
                self.assertEqual(people().STILL_REFERENCED, self.refused(
                    errors().Conflict, people().delete, CLI, person_id))
        people().delete(db.session, CLI, free)
        db.session.commit()
        self.assertEqual(2, db.session.query(Person).count())


class AttentionAndChangesTestCase(Helpers, ServiceBase):
    def test_attention_flags_the_overdue_reviews_of_current_issues_and_of_parties(self) -> None:
        older = self.issue(titulo="Más antigua", fecha_identificacion=date(2026, 1, 1),
                           fecha_revision=date(2026, 3, 1))
        late = self.issue(fecha_revision=YESTERDAY)
        self.issue(titulo="Hoy", fecha_revision=TODAY)
        self.issue(titulo="Superada", estado="superada", fecha_revision=YESTERDAY)
        clients = self.party("Clientes", fecha_revision=YESTERDAY)
        staff = self.party("Plantilla", fecha_revision=date(2026, 5, 1), pertinente=False)
        self.party("Bancos", fecha_revision=TODAY)
        self.party("Sociedad")
        flagged = ci().attention(db.session, actor(OPERATIVO), today=TODAY)
        self.assertEqual(([older.id, late.id], [staff, clients]),
                         (self.ids(flagged["cuestiones_revision_vencida"]),
                          [p.id_interesado for p in flagged["partes_revision_vencida"]]))
        with self.assertRaises(ValueError):
            ci().attention(db.session, actor(OPERATIVO), today=None)
        self.refused(errors().PermissionDenied, ci().attention, actor(ADMIN, scopes={"write"}),
                     today=TODAY)

    def stamp(self, issue_id: int, created: datetime | None, updated: datetime) -> None:
        """Set the record stamps with a core update, as an earlier write would have left them."""
        db.session.execute(CuestionContexto.__table__.update()
                           .where(CuestionContexto.id == issue_id)
                           .values(created_at=created, updated_at=updated))
        db.session.commit()

    def test_changes_counts_the_issues_new_changed_and_superseded_in_the_period(self) -> None:
        def at(day: date, hour: int = 9) -> datetime:
            return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)

        desde, hasta = date(2026, 7, 1), date(2026, 9, 30)
        before = self.issue(titulo="Antes")
        new = self.issue(titulo="Nueva", cambio_climatico=True)
        changed = self.issue(titulo="Cambiada", tipo="interna", categoria="recursos")
        superseded = self.issue(titulo="Superada", estado="superada", cambio_climatico=True)
        later = self.issue(titulo="Después")
        unknown = self.issue(titulo="Sin fecha de alta", pertinente=False,
                             justificacion_pertinencia="Irrelevante", cambio_climatico=True)
        gone_later = self.issue(titulo="Superada después", estado="superada")
        self.stamp(before.id, at(date(2026, 6, 15)), at(date(2026, 6, 20)))
        self.stamp(new.id, at(desde, 0), at(date(2026, 10, 5)))
        self.stamp(changed.id, at(date(2026, 5, 1)), at(date(2026, 8, 1)))
        self.stamp(superseded.id, at(date(2026, 5, 1)), at(hasta, 23))
        self.stamp(later.id, at(date(2026, 10, 1), 0), at(date(2026, 10, 1), 0))
        self.stamp(unknown.id, None, at(date(2026, 7, 2)))
        self.stamp(gone_later.id, at(date(2026, 5, 1)), at(date(2026, 10, 2)))
        found = ci().changes(db.session, desde, hasta)
        self.assertEqual(
            ([new.id], [changed.id, superseded.id, unknown.id], [superseded.id]),
            (self.ids(found.nuevas), sorted(self.ids(found.modificadas)),
             self.ids(found.superadas)))
        # Counted now, over the current issues: five vigentes, four of them relevant;
        # two flagged as climate-related, one of those relevant.
        self.assertEqual((5, 4, 2, 1), (found.vigentes, found.pertinentes,
                                        found.cambio_climatico,
                                        found.cambio_climatico_pertinentes))
        for desde_, hasta_ in ((hasta, desde), ("2026-07-01", hasta)):
            with self.subTest(desde=desde_, hasta=hasta_):
                with self.assertRaises(ValueError):
                    ci().changes(db.session, desde_, hasta_)


class PolicyTestCase(Helpers, ServiceBase):
    def test_every_role_reads_admins_and_auditors_write_admins_delete(self) -> None:
        issue = self.issue()
        for role in (ADMIN, AUDITOR, OPERATIVO):
            with self.subTest(read=role.name):
                self.assertEqual([issue.id], self.ids(ci().issues.list_(db.session,
                                                                         actor(role))))
        change = {"notas": "Cambio"}
        denied = ((actor(OPERATIVO), ci().issues.create, (ISSUE,), {"today": TODAY}),
                  (actor(OPERATIVO), ci().issues.update, (issue.id, change), {"today": TODAY}),
                  (actor(AUDITOR), ci().issues.delete, (issue.id,), {}),
                  (mcp_actor(ADMIN), ci().issues.delete, (issue.id,), {}),
                  (actor(AUDITOR, scopes={"read"}), ci().issues.update, (issue.id, change),
                   {"today": TODAY}),
                  (actor(AUDITOR, scopes={"write"}), ci().issues.get, (issue.id,), {}))
        for who, call, args, kwargs in denied:
            with self.subTest(denied=call.__name__, role=who.role.name, channel=who.channel):
                self.refused(errors().PermissionDenied, call, who, *args, **kwargs)
        self.edit(issue.id, change, actor(AUDITOR))
        self.edit(issue.id, {"notas": "Por MCP"}, mcp_actor(AUDITOR))
        ci().issues.create(db.session, actor(AUDITOR), ISSUE | {"titulo": "Otra"}, today=TODAY)
        ci().issues.delete(db.session, actor(ADMIN), issue.id)
        db.session.commit()
        self.assertEqual(["create", "update", "update", "delete"],
                         [r.action for r in self.rows("cuestiones_contexto")
                          if r.entity_id == issue.id])


class DatabaseCases:
    """The schema guards the issue rules itself; SQL filters run on each engine."""

    names_violated_constraints = True  # SQLite only says a foreign key failed

    def enforce_foreign_keys(self) -> None:
        """PostgreSQL always enforces them; SQLite overrides this."""

    def test_the_schema_guards_types_relevance_dates_and_links(self) -> None:
        self.enforce_foreign_keys()
        ana = self.person()
        issue = self.issue(responsable_id=ana).id
        party = self.party(responsable_id=ana)
        risk = self.risk(issue, ana).id
        params = {"i": issue, "ana": ana, "no": False, "r": risk, "p": party}
        for statement in (
            "UPDATE cuestiones_contexto SET categoria = 'cultura'",  # external issue
            "UPDATE cuestiones_contexto SET tipo = 'interna'",  # a legal issue
            "UPDATE cuestiones_contexto SET categoria = 'Legal'",  # the label is not stored
            "UPDATE cuestiones_contexto SET tipo = 'mixta'",
            "UPDATE cuestiones_contexto SET efecto = 'malo'",
            "UPDATE cuestiones_contexto SET estado = 'cerrada'",
            "UPDATE cuestiones_contexto SET pertinente = :no",  # without a justification
            "UPDATE cuestiones_contexto SET pertinente = NULL",
            "UPDATE cuestiones_contexto SET cambio_climatico = NULL",
            "UPDATE cuestiones_contexto SET titulo = NULL",
            "UPDATE cuestiones_contexto SET fecha_revision = '2026-08-31'",
            "UPDATE cuestiones_contexto SET responsable_id = 999",
            "UPDATE riesgos SET cuestion_id = 999",
            "INSERT INTO oportunidades (titulo, descripcion, origen, estado, "
            "fecha_identificacion, cuestion_id) VALUES ('O', 'O', 'externo', 'identificada', "
            "'2026-10-01', 999)",
            "UPDATE partes_interesadas SET tipo = 'Interna'",
            "UPDATE partes_interesadas SET responsable_id = 999",
            "UPDATE partes_interesadas SET requisitos_climaticos = NULL",
            "DELETE FROM cuestiones_contexto",  # cited by the risk
            "DELETE FROM personas",  # owns the issue, the party and the risk
        ):
            with self.subTest(refused=statement):
                with self.assertRaises(IntegrityError):
                    db.session.execute(text(statement), params)
                    db.session.commit()
                db.session.rollback()
        for statement in (
            "UPDATE cuestiones_contexto SET tipo = 'interna', categoria = 'cultura'",
            "UPDATE cuestiones_contexto SET pertinente = :no, "
            "justificacion_pertinencia = 'No afecta'",
            "UPDATE cuestiones_contexto SET fecha_revision = fecha_identificacion",
            "UPDATE partes_interesadas SET tipo = 'interna', pertinente = NULL",
            "UPDATE riesgos SET cuestion_id = NULL",
            "DELETE FROM cuestiones_contexto",
        ):
            with self.subTest(accepted=statement):
                db.session.execute(text(statement), params)
                db.session.commit()

    def test_foreign_keys_refuse_deletes_the_checks_did_not_see(self) -> None:
        self.enforce_foreign_keys()
        ana, eva = self.person("Ana"), self.person("Eva")
        by_risk, by_opportunity = self.issue(responsable_id=ana), self.issue(titulo="Otra")
        self.risk(by_risk.id, eva)
        self.opportunity(by_opportunity.id, eva)
        self.party(responsable_id=eva)
        named = self.names_violated_constraints
        cases = (
            (ci(), "_is_cited", ci().issues.delete, by_risk.id,
             ci().CITED_BY_RISKS if named else ci().DELETE_CONFLICT),
            (ci(), "_is_cited", ci().issues.delete, by_opportunity.id,
             ci().CITED_BY_RISKS if named else ci().DELETE_CONFLICT),
            (people(), "_is_referenced", people().delete, ana, people().STILL_REFERENCED),
        )
        for module, check, delete, record_id, expected in cases:
            with self.subTest(check=check, record=record_id):
                with patch.object(module, check, return_value=False):
                    with self.assertRaises(errors().Conflict) as caught:
                        delete(db.session, CLI, record_id)
                db.session.rollback()
                self.assertIsInstance(caught.exception.__cause__, IntegrityError)
                self.assertEqual(expected, caught.exception.message)
        self.assertEqual((2, 2), (db.session.query(CuestionContexto).count(),
                                  db.session.query(Person).count()))

    def test_the_filters_attention_and_changes_run_in_the_database(self) -> None:
        self.enforce_foreign_keys()
        late = self.issue(fecha_revision=YESTERDAY).id
        self.issue(titulo="Clima", categoria="ambiental", cambio_climatico=True)
        party = self.party(fecha_revision=YESTERDAY, tipo=TipoContexto.externa, pertinente=True)
        who = actor(OPERATIVO, user_id=None)
        self.assertEqual([late], self.ids(ci().issues.list_(
            db.session, who, revision_vencida=True, today=TODAY, tipo="externa",
            pertinente=True, cambio_climatico=False, estado="vigente")))
        flagged = ci().attention(db.session, who, today=TODAY)
        self.assertEqual(([late], [party]),
                         (self.ids(flagged["cuestiones_revision_vencida"]),
                          [p.id_interesado for p in flagged["partes_revision_vencida"]]))
        found = ci().changes(db.session, date(2026, 1, 1), date(2099, 12, 31))
        self.assertEqual((2, 0, 2, 2, 1), (len(found.nuevas), len(found.modificadas),
                                           found.vigentes, found.pertinentes,
                                           found.cambio_climatico))


class SqliteDatabaseTestCase(DatabaseCases, Helpers, ServiceBase):
    names_violated_constraints = False

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
class PostgresDatabaseTestCase(DatabaseCases, Helpers, unittest.TestCase):
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


class McpTestCase(McpDbCase):
    async def test_issues_parties_and_risk_links_round_trip(self) -> None:
        ana = self.seed("personas")
        who = mcp_actor(AUDITOR)
        payload = {**ISSUE, "fecha_identificacion": "2026-09-01", "responsable_id": ana,
                   "fecha_revision": "2026-10-20"}
        with patch("app.mcp_server.registry.local_today", return_value=TODAY):
            created = await self.call(who, "qms_create", {"module": "cuestiones_contexto",
                                                          "data": payload})
            self.assertFalse(created.is_error, created.content)
            issue = created.structured_content
            self.assertEqual(
                ("Externa", "Legal", "Negativo", "Vigente", True, False, [], []),
                tuple(issue[k] for k in ("tipo", "categoria", "efecto", "estado",
                                         "revision_vencida", "cambio_climatico", "riesgos",
                                         "oportunidades")))
            for data, word in (({"categoria": "cultura"}, "categoría"),
                               ({"pertinente": False}, "pertinente"),
                               ({"fecha_identificacion": "2026-10-22"}, "futura")):
                with self.subTest(refused=data):
                    result = await self.call(who, "qms_create", {
                        "module": "cuestiones_contexto", "data": payload | data})
                    self.assertTrue(result.is_error)
                    self.assertIn(word, result.content[0].text)
            risk = await self.call(who, "qms_create", {"module": "riesgos", "data": RISK | {
                "responsable_id": ana, "cuestion_id": issue["id"]}})
            self.assertFalse(risk.is_error, risk.content)
            self.assertEqual(issue["id"], risk.structured_content["cuestion_id"])
            got = (await self.call(who, "qms_get", {"module": "cuestiones_contexto",
                                                    "id": issue["id"]})).structured_content
            self.assertEqual(([risk.structured_content["id"]], []),
                             (got["riesgos"], got["oportunidades"]))
            for slug, filters, total in (
                ("cuestiones_contexto", {"tipo": "externa", "categoria": "legal",
                                         "pertinente": True, "cambio_climatico": False,
                                         "estado": "vigente", "responsable_id": ana,
                                         "revision_vencida": True}, 1),
                ("cuestiones_contexto", {"tipo": "interna"}, 0),
                ("riesgos", {"cuestion_id": issue["id"]}, 1),
                ("oportunidades", {"cuestion_id": issue["id"]}, 0),
            ):
                with self.subTest(slug=slug, filters=filters):
                    listed = await self.call(mcp_actor(OPERATIVO), "qms_list",
                                             {"module": slug, "filters": filters})
                    self.assertFalse(listed.is_error, listed.content)
                    self.assertEqual(total, listed.structured_content["total"])
            superseded = await self.call(who, "qms_update", {
                "module": "cuestiones_contexto", "id": issue["id"],
                "data": {"estado": "superada"}})
            self.assertEqual(("Superada", False),
                             (superseded.structured_content["estado"],
                              superseded.structured_content["revision_vencida"]))
            relinked = await self.call(who, "qms_create", {"module": "oportunidades", "data": {
                **OPPORTUNITY, "responsable_id": ana, "cuestion_id": issue["id"]}})
            self.assertTrue(relinked.is_error)
            self.assertIn("superada", relinked.content[0].text)
            party = {"nombre": "Clientes", "tipo": "externa", "pertinente": None,
                     "responsable_id": ana, "requisitos_climaticos": True}
            past = await self.call(mcp_actor(OPERATIVO), "qms_create", {
                "module": "partes_interesadas", "data": party | {"fecha_revision": "2026-10-20"}})
            self.assertTrue(past.is_error)
            self.assertIn("anterior a hoy", past.content[0].text)
            saved = await self.call(mcp_actor(OPERATIVO), "qms_create", {
                "module": "partes_interesadas", "data": party | {"fecha_revision": "2026-11-30"}})
            self.assertFalse(saved.is_error, saved.content)
            self.assertEqual(("Externa", None, ana, "2026-11-30", True), tuple(
                saved.structured_content[k] for k in ("tipo", "pertinente", "responsable_id",
                                                      "fecha_revision",
                                                      "requisitos_climaticos")))
            denied = await self.call(mcp_actor(OPERATIVO), "qms_create", {
                "module": "cuestiones_contexto", "data": payload})
            self.assertTrue(denied.is_error)
            self.assertIn("No tienes permiso", denied.content[0].text)
        modules = (await self.call(mcp_actor(ADMIN), "qms_modules")).structured_content
        described = {m["slug"]: m for m in modules["modules"]}
        fields = {f["name"]: f for f in described["cuestiones_contexto"]["fields"]}
        self.assertEqual({"tipo", "categoria", "titulo", "descripcion", "efecto", "pertinente",
                          "fecha_identificacion"},
                         {name for name, f in fields.items() if f["required"]})
        self.assertEqual((["interna", "externa"], ["vigente", "superada"], "boolean"),
                         (fields["tipo"]["allowed"], fields["estado"]["allowed"],
                          fields["cambio_climatico"]["type"]))
        self.assertEqual(11, len(fields["categoria"]["allowed"]))
        self.assertEqual({"tipo", "categoria", "pertinente", "cambio_climatico", "estado",
                          "responsable_id", "revision_vencida"},
                         {f["name"] for f in described["cuestiones_contexto"]["filters"]})
        self.assertEqual({"read": True, "create": True, "update": True},
                         described["cuestiones_contexto"]["permissions"])
        parties = {f["name"]: f["type"] for f in described["partes_interesadas"]["fields"]}
        self.assertLessEqual({("tipo", "enum"), ("pertinente", "boolean"),
                              ("responsable_id", "integer"), ("fecha_revision", "date"),
                              ("requisitos_climaticos", "boolean")}, set(parties.items()))
        for slug in ("riesgos", "oportunidades"):
            with self.subTest(slug=slug):
                self.assertIn("cuestion_id",
                              {f["name"] for f in described[slug]["fields"]})
        tools = {tool.name for tool in await self.list_tools()}
        self.assertFalse({name for name in tools if "delete" in name})


if __name__ == "__main__":
    unittest.main()
