"""Risk/opportunity effectiveness through the public management review input API."""
from datetime import date, timedelta

from app.extensions import db
from app.models import AccionRiesgo, Oportunidad, Person, Riesgo, RoleEnum
from app.services import management_reviews as mr
from app.services.actor import Actor
from app.services.errors import PermissionDenied
from test_communication_independent import hashes
from test_conformity_analysis import ConformityFixtures, START, END, TODAY, ADMIN
from test_nonconformity_service import ServiceBase


class RiskEffectivenessFixtures(ConformityFixtures):
    """Core source evidence independent of the review commands under test."""

    def risk_action(self, day: date | None = START, result: str | None = None,
                    kind: str = "riesgo", state: str = "completada",
                    description: str = "Qualify supplier", title: str = "Supply",
                    evaluated: date = TODAY) -> int:
        """Create one valid action and its parent with unrelated identification dates."""
        if db.session.get(Person, 1) is None:
            self.source(Person, nombre="Owner")
        parent = self.source(Riesgo if kind == "riesgo" else Oportunidad,
                             titulo=title, descripcion="Source", origen="externo",
                             fecha_identificacion=date(2020, 1, 1))
        return self.source(
            AccionRiesgo, **{f"{kind}_id": parent}, descripcion=description,
            responsable_id=1, fecha_limite=date(2020, 1, 1), estado=state,
            fecha_completada=day if state == "completada" else None,
            eficacia=result, fecha_evaluacion=evaluated if result else None)

    def effectiveness(self, **kwargs: object) -> dict:
        """Read using the review's existing authorization and date validation."""
        return mr.inputs(db.session, ADMIN, START, END, today=TODAY, **kwargs).get(
            "eficacia_riesgos", {})


class RiskEffectivenessRules(RiskEffectivenessFixtures, ServiceBase):
    def test_period_uses_inclusive_completion_dates_and_only_completed_actions(self) -> None:
        for day in (START, END, START - timedelta(days=1), END + timedelta(days=1)):
            self.risk_action(day, "eficaz")  # evaluation outside the period
        for state in ("pendiente", "en_curso", "cancelada"):
            self.risk_action(None, state=state)
        # Evaluation in the period cannot pull an older completion into it.
        self.risk_action(START - timedelta(days=1), "no_eficaz", evaluated=START)
        found = self.effectiveness()
        self.assertEqual({"total": 2, "eficaz": 2, "no_eficaz": 0, "sin_evaluar": 0},
                         found.get("totales"))
        early = mr.inputs(db.session, ADMIN, START, END, today=START)
        self.assertEqual(found, early["eficacia_riesgos"])

    def test_effectiveness_results_are_disjoint_and_empty_period_has_zeros(self) -> None:
        for result in ("eficaz", "no_eficaz", None):
            self.risk_action(result=result)
        self.assertEqual({"total": 3, "eficaz": 1, "no_eficaz": 1, "sin_evaluar": 1},
                         self.effectiveness().get("totales"))
        empty = mr.inputs(db.session, ADMIN, date(2025, 1, 1), date(2025, 1, 31),
                          today=TODAY)["eficacia_riesgos"]
        self.assertEqual({"total": 0, "eficaz": 0, "no_eficaz": 0, "sin_evaluar": 0},
                         empty["totales"])
        self.assertEqual([], empty["no_eficaces_lista"])

    def test_risks_and_opportunities_have_separate_complete_counts(self) -> None:
        for kind, results in (("riesgo", ("eficaz", None)),
                              ("oportunidad", ("no_eficaz", "no_eficaz", None))):
            for result in results:
                self.risk_action(result=result, kind=kind)
        self.assertEqual({
            "riesgos": {"total": 2, "eficaz": 1, "no_eficaz": 0, "sin_evaluar": 1},
            "oportunidades": {"total": 3, "eficaz": 0, "no_eficaz": 2, "sin_evaluar": 1},
        }, self.effectiveness().get("por_tipo"))

    def test_ineffective_list_is_bounded_ordered_and_names_its_parent(self) -> None:
        ids = [self.risk_action(END, "no_eficaz", description=f"Action {i:02d}" + "x" * 130,
                               title=f"Parent {i:02d}" + "y" * 130,
                               kind="oportunidad" if i % 2 else "riesgo")
               for i in range(mr.LIST_LIMIT + 1)]
        self.risk_action(START, "no_eficaz", description="Older")
        self.risk_action(result="eficaz", description="Effective")
        self.risk_action(description="Not evaluated")
        found = self.effectiveness()
        self.assertEqual(12, found.get("totales", {}).get("no_eficaz"))
        listed = found["no_eficaces_lista"]
        self.assertEqual(ids[:mr.LIST_LIMIT], [row["id"] for row in listed])
        from app.routes.management_review_routes import input_sections

        block = next(block for section in input_sections({"eficacia_riesgos": found})
                     for block in section["bloques"]
                     if block["titulo"] == "Acciones no eficaces")
        self.assertEqual("Se muestran los 10 más recientes de 12.", block["nota"])
        for i, row in enumerate(listed):
            self.assertEqual("oportunidad" if i % 2 else "riesgo", row["tipo"])
            self.assertEqual(i // 2 + 1, row["registro_id"])
            for key, prefix in (("descripcion", "Action"), ("titulo", "Parent")):
                self.assertEqual(120, len(row[key]))
                self.assertTrue(row[key].startswith(f"{prefix} {i:02d}"))
                self.assertTrue(row[key].endswith("…"))
            self.assertEqual(END.isoformat(), row["fecha_completada"])

    def test_permissions_and_read_only_match_other_review_inputs(self) -> None:
        self.risk_action(result="no_eficaz")
        before = hashes()
        for role in (RoleEnum.ADMINISTRADOR, RoleEnum.AUDITOR):
            for channel in ("web", "mcp"):
                who = Actor(None, "reader", role, channel, scopes=frozenset({"read"}))
                found = mr.inputs(db.session, who, START, END, today=TODAY)
                self.assertIn("eficacia_riesgos", found)
                self.assertIn("conformidad", found)  # preserve T1
        for who in (Actor(None, "worker", RoleEnum.OPERATIVO, "web"),
                    Actor(None, "writer", RoleEnum.ADMINISTRADOR, "mcp",
                          scopes=frozenset({"write"}))):
            with self.assertRaises(PermissionDenied):
                mr.inputs(db.session, who, START, END, today=TODAY)
        self.assertEqual(before, hashes())

    def test_holding_freezes_counts_results_descriptions_and_parent_names(self) -> None:
        from app.services import risks

        action = self.risk_action(description="Original", title="Original parent")
        listed = self.risk_action(result="no_eficaz", description="Listed original",
                                  title="Listed parent")
        review = mr.reviews.create(db.session, ADMIN, {
            "fecha": END, "periodo_desde": START, "periodo_hasta": END,
            "asistente_ids": [1],
        })
        db.session.commit()
        live = mr.preview(db.session, ADMIN, review, today=TODAY)
        self.assertIn("eficacia_riesgos", live)
        mr.hold(db.session, ADMIN, review.id, today=TODAY)
        db.session.commit()
        risks.actions.update(db.session, ADMIN, action, {
            "descripcion": "Changed", "eficacia": "no_eficaz", "fecha_evaluacion": TODAY,
        }, today=TODAY)
        risks.actions.update(db.session, ADMIN, listed, {
            "descripcion": "Listed changed", "eficacia": "eficaz",
        }, today=TODAY)
        for parent_id in (1, 2):
            risks.risks.update(db.session, ADMIN, parent_id,
                               {"titulo": f"Changed parent {parent_id}"}, today=TODAY)
        db.session.commit()
        self.risk_action(result="eficaz")
        before = hashes()
        self.assertEqual(live, mr.preview(db.session, ADMIN, review, today=TODAY))
        self.assertNotEqual(live["eficacia_riesgos"], self.effectiveness())
        self.assertEqual(before, hashes())
