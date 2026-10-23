"""Management review screens (task MR-2 of ``management-review``).

Through the web: a draft review is created with its period and attendees; its
page shows the inputs of 9.3.2 live, with a provisional note, next to the
comment of each input; actions are added with an owner and a deadline. An
administrator holds the review, which freezes the inputs (later register
changes leave them as presented), writes the conclusions and closes it; then
only the follow-up of each action changes. An overdue action carries a badge.
Administrators and auditors read and write; only administrators hold, close
and delete (decisions MR4 to MR6); other roles are refused everywhere. A
refused write re-renders the form with the input kept. Every test runs with
the audit flush guard. The action register and the dashboard card are tested
in ``test_dashboard``, so they can ship in their own commit.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM, ScreensBase
from test_delete_forms import _PostForms

from app.extensions import db
from app.forms import REVIEW_INPUTS
from app.models import (
    AccionRevision, AlcanceSGC, CuestionContexto, EstadoAccionRevision, EstadoAlcance,
    EstadoRevisionDireccion, GravedadNoConformidad, NoConformidad, Oportunidad,
    RevisionDireccion, Riesgo,
)
from app.routes.management_review_routes import input_sections
from app.services import management_reviews as mr
from app.services import nonconformities, providers

BASE = "/revisiones-direccion"
TODAY = date(2026, 10, 12)
ROUTES = "app.routes.management_review_routes"
PROVISIONAL = "Datos provisionales: se congelarán al celebrar la revisión"
OVERDUE = '<span class="badge badge--danger">Vencida</span>'
REVIEW_FORM = {"fecha": "2026-07-10", "periodo_desde": "2026-04-01",
               "periodo_hasta": "2026-06-30", "comentario_acciones_previas": "",
               "comentario_contexto": "Nuevo competidor en la zona", "comentario_desempeno": "",
               "comentario_recursos": "", "comentario_riesgos": "", "comentario_mejora": ""}
COMMENTS = {name: REVIEW_FORM[name] for name in mr.COMMENT_FIELDS}
# The risk inputs a review held before RO-2 of ``risks-opportunities`` froze (RO7).
OLD_FROZEN = {
    "periodo": {"desde": "2026-04-01", "hasta": "2026-06-30"}, "fecha_corte": "2026-06-30",
    "contexto": {
        "partes_interesadas": {"total": 1, "nuevas": 0, "modificadas": 0, "lista": []},
        "riesgos_oportunidades": {
            "por_tipo": {"Riesgo": 2, "Oportunidad": 1}, "nuevos": 1, "modificados": 1,
            "lista": [{"tipo": "Riesgo", "descripcion": "Fallo de proveedor"},
                      {"tipo": "Oportunidad", "descripcion": "Nuevo mercado"}]},
    },
}
ACTION_FORM = {"tipo": "recursos", "descripcion": "Contratar un técnico de calidad",
               "fecha_limite": "2026-10-31", "estado": "pendiente", "fecha_completada": "",
               "resultado": ""}


class ReviewScreensBase(ScreensBase):
    """``ScreensBase`` with today pinned in the review and dashboard routes."""

    def setUp(self) -> None:
        super().setUp()
        for target in (f"{ROUTES}.local_today", "app.routes.dashboard_routes.local_today"):
            patcher = patch(target, return_value=TODAY)
            patcher.start()
            self.addCleanup(patcher.stop)

    def review(self, attendees=(), **values) -> int:
        """A draft review of the second quarter of 2026, created through the service."""
        payload = {"fecha": date(2026, 7, 10), "periodo_desde": date(2026, 4, 1),
                   "periodo_hasta": date(2026, 6, 30), "asistente_ids": list(attendees)}
        with self.app.app_context():
            found = mr.reviews.create(db.session, SYSTEM, payload | values)
            db.session.commit()
            return found.id

    def action(self, review_id: int, **values) -> int:
        """An action owned by Eva, created through the service."""
        payload = {"revision_id": review_id, "tipo": "mejora",
                   "descripcion": "Automatizar pedidos", "responsable_id": self.eva,
                   "fecha_limite": date(2026, 12, 31)}
        with self.app.app_context():
            found = mr.actions.create(db.session, SYSTEM, payload | values, today=TODAY)
            db.session.commit()
            return found.id

    def advance(self, review_id: int, *steps: str) -> None:
        """Run ``hold`` and ``close`` through the service; closing writes conclusions first."""
        with self.app.app_context():
            for step in steps:
                if step == "close":
                    mr.reviews.update(db.session, SYSTEM, review_id, {"conclusiones": "Conforme"})
                getattr(mr, step)(db.session, SYSTEM, review_id, today=TODAY)
            db.session.commit()

    def held_with(self, frozen: dict) -> int:
        """A review held with ``frozen`` inputs (core insert: an older shape, RO7)."""
        with self.app.app_context():
            result = db.session.execute(RevisionDireccion.__table__.insert().values(
                fecha=date(2026, 7, 10), periodo_desde=date(2026, 4, 1),
                periodo_hasta=date(2026, 6, 30), estado=EstadoRevisionDireccion.celebrada,
                datos_entrada=frozen))
            db.session.commit()
            return result.inserted_primary_key[0]

    def registers(self) -> None:
        """A critical risk Eva owns, an unassessed one and a high-priority opportunity,
        all created in the second quarter (core inserts: source records)."""
        stamp = datetime(2026, 4, 2, 9, tzinfo=timezone.utc)
        base = {"descripcion": "d", "origen": "interno", "fecha_identificacion": date(2026, 4, 2),
                "created_at": stamp, "updated_at": stamp}
        with self.app.app_context():
            for values in ({"titulo": "Fallo de proveedor", "probabilidad": 4, "impacto": 5,
                            "responsable_id": self.eva}, {"titulo": "Sin valorar"}):
                db.session.execute(Riesgo.__table__.insert().values(**(base | values)))
            db.session.execute(Oportunidad.__table__.insert().values(
                **base, titulo="Nuevo mercado", beneficio=5, viabilidad=4))
            db.session.commit()

    def nonconformity(self, fecha: date) -> None:
        """A major nonconformity (core insert: a source record, not under test)."""
        with self.app.app_context():
            db.session.execute(NoConformidad.__table__.insert().values(
                descripcion="Retraso", fecha_detectada=fecha,
                gravedad=GravedadNoConformidad.mayor))
            db.session.commit()

    def suppliers(self) -> None:
        """An approved provider rated apto and a pending one rated no apto in the second
        quarter, and two nonconformities with the provider origin, one naming the first."""
        with self.app.app_context():
            aceros, transportes = (providers.providers.create(db.session, SYSTEM, {
                "nombre": nombre, "tipo_suministro": "producto"}).id
                for nombre in ("Aceros del Norte", "Transportes Rápidos"))
            providers.approve(db.session, SYSTEM, aceros, today=TODAY)
            for provider, fecha, scores in ((aceros, date(2026, 5, 10), (4, 4, 4, 4)),
                                            (transportes, date(2026, 6, 20), (2, 2, 2, 3))):
                providers.evaluations.create(db.session, SYSTEM, {
                    "proveedor_id": provider, "fecha": fecha, "evaluador_id": self.eva,
                    **dict(zip(("calidad", "plazo", "servicio", "documentacion"), scores))},
                    today=TODAY)
            for provider, fecha in ((aceros, date(2026, 5, 12)), (None, date(2026, 5, 13))):
                nonconformities.create(db.session, SYSTEM, {
                    "descripcion": "Lote con rebabas", "fecha_detectada": fecha,
                    "origen": "proveedor", "proveedor_id": provider})
            db.session.commit()

    def before_providers(self) -> dict:
        """The second quarter's live inputs without ``proveedores``: the shape frozen
        before EP-3 of ``external-providers``."""
        with self.app.app_context():
            live = mr.inputs(db.session, SYSTEM, date(2026, 4, 1), date(2026, 6, 30),
                             today=TODAY)
        return {key: value for key, value in live.items() if key != "proveedores"}

    def context(self) -> None:
        """A relevant climate-related issue created in the second quarter, an older one
        superseded in it and version 2 of the scope in force since 4 May 2026 (core
        inserts: source records)."""
        utc = timezone.utc
        base = {"tipo": "externa", "categoria": "ambiental", "descripcion": "d",
                "efecto": "negativo", "pertinente": True, "fecha_identificacion": date(2026, 4, 2)}
        issues = CuestionContexto.__table__
        with self.app.app_context():
            db.session.execute(issues.insert().values(
                **base, titulo="Sequías prolongadas", cambio_climatico=True,
                created_at=datetime(2026, 4, 2, 9, tzinfo=utc),
                updated_at=datetime(2026, 4, 2, 9, tzinfo=utc)))
            db.session.execute(issues.insert().values(
                **(base | {"categoria": "legal"}), titulo="Normativa derogada",
                estado="superada", created_at=datetime(2025, 1, 1, tzinfo=utc),
                updated_at=datetime(2026, 5, 2, tzinfo=utc)))
            db.session.execute(AlcanceSGC.__table__.insert().values(
                version=2, estado=EstadoAlcance.vigente, declaracion="Envases metálicos",
                aprobado_por_id=self.eva, fecha_aprobacion=date(2026, 5, 4)))
            db.session.commit()

    def before_context(self) -> dict:
        """The second quarter's live inputs without the context issues and the scope: the
        shape frozen before OC-4 of ``organization-context``."""
        with self.app.app_context():
            live = mr.inputs(db.session, SYSTEM, date(2026, 4, 1), date(2026, 6, 30),
                             today=TODAY)
        live["contexto"] = {key: value for key, value in live["contexto"].items()
                            if key not in ("cuestiones", "alcance")}
        return live

    def action_id(self, descripcion: str) -> int:
        with self.app.app_context():
            return db.session.query(AccionRevision.id).filter_by(descripcion=descripcion).scalar()

    def inputs(self, review_id: int) -> str:
        match = re.search(r'<section id="entradas"(.*?)</section>',
                          self.html(f"{BASE}/{review_id}"), re.S)
        self.assertIsNotNone(match, "the inputs section is missing")
        return match.group(1)

    def figure(self, html: str, label: str) -> str:
        match = re.search(rf'<span class="list__primary">{re.escape(label)}</span>\s*'
                          r'<span class="list__secondary">([^<]*)</span>', html)
        self.assertIsNotNone(match, f"no figure «{label}»")
        return match.group(1).strip()

    def row(self, html: str, kind: str, record_id: int) -> str:
        match = re.search(rf'<tr id="{kind}-{record_id}">(.*?)</tr>', html, re.S)
        self.assertIsNotNone(match, f"no row {kind}-{record_id}")
        return match.group(1)

    def listed(self, url: str, kind: str) -> set[int]:
        return set(map(int, re.findall(rf'<tr id="{kind}-(\d+)">', self.html(url))))


class WebFlowTestCase(ReviewScreensBase):
    def test_draft_preview_actions_hold_freeze_conclusions_close_and_follow_up(self) -> None:
        self.nonconformity(date(2026, 5, 10))
        self.login(ADMIN)
        html = self.html(f"{BASE}/nueva")
        for marker in ('name="asistente_ids"', "multiple", ">Eva Ruiz</option>",
                       'name="comentario_mejora"'):
            self.assertIn(marker, html)
        self.assertNotIn('name="conclusiones"', html)
        response = self.post(f"{BASE}/nueva",
                             REVIEW_FORM | {"asistente_ids": [str(self.ana), str(self.luis)]})
        rid = int(re.fullmatch(rf"{BASE}/(\d+)", response.headers["Location"]).group(1))

        # A draft shows its inputs live, with the provisional note.
        page = self.html(f"{BASE}/{rid}")
        for text in ("Borrador", PROVISIONAL, "10/07/2026", "01/04/2026 – 30/06/2026",
                     "Ana Pérez", "Luis Gil", "Nuevo competidor en la zona",
                     "a) Estado de las acciones de las revisiones previas",
                     "f) Oportunidades de mejora", "Sin conclusiones todavía."):
            self.assertIn(text, page)
        self.assertEqual("1", self.figure(self.inputs(rid), "Detectadas en el periodo"))
        self.assertEqual("1", self.figure(self.inputs(rid), "Gravedad: Mayor"))
        self.nonconformity(date(2026, 6, 1))
        self.assertEqual("2", self.figure(self.inputs(rid), "Detectadas en el periodo"))

        # Two actions: one due at the end of the month, one already overdue.
        self.assertIn('name="estado"', self.html(f"{BASE}/{rid}/acciones/nueva"))
        self.post(f"{BASE}/{rid}/acciones/nueva", ACTION_FORM | {"responsable_id": str(self.eva)},
                  expect=f"{BASE}/{rid}")
        self.post(f"{BASE}/{rid}/acciones/nueva", ACTION_FORM | {
            "tipo": "mejora", "descripcion": "Revisar el plan de formación",
            "responsable_id": str(self.luis), "fecha_limite": "2026-09-30"},
            expect=f"{BASE}/{rid}")
        first = self.action_id("Contratar un técnico de calidad")
        late = self.action_id("Revisar el plan de formación")
        page = self.html(f"{BASE}/{rid}")
        for text in ("Necesidad de recursos", "Eva Ruiz", "31/10/2026", "Pendiente"):
            self.assertIn(text, self.row(page, "accion", first))
        self.assertNotIn(OVERDUE, self.row(page, "accion", first))
        self.assertIn(OVERDUE, self.row(page, "accion", late))

        # Holding freezes the inputs: later register changes leave them as presented.
        self.post(f"{BASE}/{rid}/celebrar", expect=f"{BASE}/{rid}")
        stored = self.load(RevisionDireccion, rid)
        self.assertEqual((EstadoRevisionDireccion.celebrada, self.ana),
                         (stored.estado, stored.celebrada_por_id))
        frozen = self.inputs(rid)
        self.assertEqual("2", self.figure(frozen, "Detectadas en el periodo"))
        self.nonconformity(date(2026, 6, 15))
        self.assertEqual(frozen, self.inputs(rid))
        page = self.html(f"{BASE}/{rid}")
        self.assertNotIn(PROVISIONAL, page)
        self.assertIn("Datos congelados al celebrar la revisión", page)
        self.assertEqual("Ana Pérez", self.figure(page, "Celebrada por"))

        # A held review takes only comments and conclusions.
        self.assertEqual(302, self.client.get(f"{BASE}/{rid}/editar").status_code)
        self.assertIn(("danger", mr.ONLY_COMMENTS), self.flashes())
        html = self.html(f"{BASE}/{rid}/conclusiones")
        self.assertIn('name="conclusiones"', html)
        self.assertNotIn('name="fecha"', html)
        self.post(f"{BASE}/{rid}/conclusiones", COMMENTS | {
            "comentario_mejora": "Digitalizar los registros",
            "conclusiones": "El SGC es conveniente, adecuado y eficaz."}, expect=f"{BASE}/{rid}")

        # Closing records today; the actions then take only their follow-up.
        self.post(f"{BASE}/{rid}/cerrar", expect=f"{BASE}/{rid}")
        stored = self.load(RevisionDireccion, rid)
        self.assertEqual((EstadoRevisionDireccion.cerrada, TODAY),
                         (stored.estado, stored.fecha_cierre))
        page = self.html(f"{BASE}/{rid}")
        for text in ("Cerrada", "El SGC es conveniente, adecuado y eficaz.",
                     "Digitalizar los registros", "Nuevo competidor en la zona"):
            self.assertIn(text, page)
        self.assertEqual("12/10/2026", self.figure(page, "Fecha de cierre"))
        for marker in (f'action="{BASE}/{rid}/cerrar"', f'href="{BASE}/{rid}/conclusiones"',
                       f'href="{BASE}/{rid}/acciones/nueva"',
                       f'action="{BASE}/{rid}/acciones/{first}/eliminar"'):
            self.assertNotIn(marker, page)
        html = self.html(f"{BASE}/{rid}/acciones/{first}/editar")
        for name in ("estado", "fecha_completada", "resultado"):
            self.assertIn(f'name="{name}"', html)
        for name in ("tipo", "descripcion", "responsable_id", "fecha_limite"):
            self.assertNotIn(f'name="{name}"', html)
        self.assertIn("Contratar un técnico de calidad", html)
        self.post(f"{BASE}/{rid}/acciones/{first}/editar", {
            "estado": "completada", "fecha_completada": "2026-10-11",
            "resultado": "Técnico incorporado"}, expect=f"{BASE}/{rid}")
        done = self.load(AccionRevision, first)
        self.assertEqual(
            (EstadoAccionRevision.completada, date(2026, 10, 11), "Técnico incorporado"),
            (done.estado, done.fecha_completada, done.resultado))
        page = self.html(f"{BASE}/{rid}")
        for text in ("Completada", "11/10/2026", "Técnico incorporado"):
            self.assertIn(text, self.row(page, "accion", first))
        self.assertNotIn(OVERDUE, self.row(page, "accion", first))
        self.assertIn(OVERDUE, self.row(page, "accion", late))

    def test_the_inputs_list_earlier_actions_and_tolerate_missing_keys(self) -> None:
        earlier = self.review([self.ana], fecha=date(2026, 4, 2), periodo_desde=date(2026, 1, 1),
                              periodo_hasta=date(2026, 3, 31))
        self.action(earlier, descripcion="Renovar el ERP", fecha_limite=date(2026, 5, 31))
        rid = self.review([self.ana])
        self.login(AUDITOR)
        inputs = self.inputs(rid)
        self.assertEqual("1", self.figure(inputs, "Abiertas al cierre del periodo"))
        self.assertEqual("1", self.figure(inputs, "Vencidas al cierre"))
        for text in ("02/04/2026", "Oportunidad de mejora", "Renovar el ERP", "Eva Ruiz",
                     "31/05/2026 (vencida)", "Pendiente"):
            self.assertIn(text, inputs)
        sections = input_sections({})
        self.assertEqual(list(REVIEW_INPUTS.values()), [s["titulo"] for s in sections])
        self.assertEqual({"—"}, {value for s in sections for b in s["bloques"]
                                 for _, value in b["cifras"]})

    def test_the_inputs_show_the_risk_registers_and_old_frozen_risks_still_render(self) -> None:
        self.registers()
        rid = self.review([self.ana])
        self.login(AUDITOR)
        inputs = self.inputs(rid)
        for label, value in (("Riesgos registrados", "2"), ("Nivel: Crítico", "1"),
                             ("Nivel: Bajo", "0"), ("Nivel: Sin valorar", "1"),
                             ("Riesgos nuevos en el periodo", "2"),
                             ("Abiertos con nivel alto o crítico", "1"),
                             ("Oportunidades registradas", "1"), ("Prioridad: Alta", "1"),
                             ("Oportunidades nuevas en el periodo", "1")):
            with self.subTest(label=label):
                self.assertEqual(value, self.figure(inputs, label))
        for text in ("Fallo de proveedor", "Eva Ruiz", "Identificado", "Nuevo mercado",
                     "Identificada"):
            self.assertIn(text, inputs)
        self.assertNotIn("Registrados: Riesgo", inputs)
        old = self.html(f"{BASE}/{self.held_with(OLD_FROZEN)}")
        for label, value in (("Registrados: Riesgo", "2"), ("Registrados: Oportunidad", "1"),
                             ("Nuevos en el periodo", "1"), ("Modificados en el periodo", "1")):
            with self.subTest(old=label):
                self.assertEqual(value, self.figure(old, label))
        for text in ("Fallo de proveedor", "Nuevo mercado"):
            self.assertIn(text, old)
        self.assertNotIn("Nivel:", old)
        titles = [b["titulo"] for s in input_sections(OLD_FROZEN) for b in s["bloques"]]
        self.assertIn("Riesgos y oportunidades", titles)
        self.assertNotIn("Oportunidades", titles)

    def test_the_inputs_show_the_providers_and_reviews_held_before_them_still_render(
            self) -> None:
        self.suppliers()
        rid = self.review([self.ana])
        self.login(AUDITOR)
        inputs = self.inputs(rid)
        for label, value in (("Estado actual: Aprobado", "1"), ("Estado actual: Pendiente", "1"),
                             ("Evaluaciones vencidas al cierre", "0"),
                             ("Evaluaciones en el periodo", "2"),
                             ("Puntuación media de las evaluaciones", "3,13"),
                             ("Resultado: Apto", "1"), ("Resultado: No apto", "1"),
                             ("Proveedores no aptos en el periodo", "1"),
                             ("Detectadas en el periodo con origen proveedor", "2"),
                             ("Sin proveedor indicado", "1")):
            with self.subTest(label=label):
                self.assertEqual(value, self.figure(inputs, label))
        for text in ("Proveedores externos", "No conformidades de proveedores",
                     "Transportes Rápidos", "20/06/2026", "2,25", "Aprobado"):
            self.assertIn(text, inputs)
        # Inputs frozen before the providers section (EP-3) show none of it.
        old = self.html(f"{BASE}/{self.held_with(self.before_providers())}")
        self.assertEqual("2", self.figure(old, "Detectadas en el periodo"))
        for text in ("Proveedores externos", "No conformidades de proveedores",
                     "Estado actual:"):
            self.assertNotIn(text, old)
        for frozen in (OLD_FROZEN, {}):
            titles = [b["titulo"] for s in input_sections(frozen) for b in s["bloques"]]
            self.assertNotIn("Proveedores externos", titles)

    def test_the_inputs_show_the_context_issues_and_scope_unless_held_before_them(
            self) -> None:
        """OC5 of ``organization-context``: input 9.3.2 b gains the context issues and
        the scope in force; reviews held before froze neither and show neither."""
        self.context()
        rid = self.review([self.ana])
        old = self.held_with(self.before_context())
        self.login(AUDITOR)
        inputs = self.inputs(rid)
        for label, value in (("Cuestiones nuevas en el periodo", "1"),
                             ("Cuestiones modificadas en el periodo", "1"),
                             ("Cuestiones superadas en el periodo", "1"),
                             ("Cuestiones vigentes", "1"), ("Vigentes pertinentes", "1"),
                             ("Vigentes sobre el cambio climático", "1"),
                             ("Pertinentes sobre el cambio climático", "1"),
                             ("Versión vigente del alcance", "2"),
                             ("Fecha de aprobación del alcance", "04/05/2026")):
            with self.subTest(label=label):
                self.assertEqual(value, self.figure(inputs, label))
        for cells in (("Sequías prolongadas", "Nueva", "Externa", "Ambiental", "Sí", "Sí",
                       "Vigente"),
                      ("Normativa derogada", "Modificada", "Externa", "Legal", "Sí", "No",
                       "Superada")):
            self.assertIn("".join(f"<td>{cell}</td>" for cell in cells), inputs)
        old_inputs = self.inputs(old)
        self.assertEqual("0", self.figure(old_inputs, "Riesgos registrados"))
        for text in ("Cuestiones externas e internas", "Cuestiones vigentes",
                     "Alcance del SGC", "Versión vigente del alcance", "Sequías prolongadas"):
            self.assertNotIn(text, old_inputs)
        for frozen in (OLD_FROZEN, {}):
            titles = [b["titulo"] for s in input_sections(frozen) for b in s["bloques"]]
            self.assertNotIn("Cuestiones externas e internas", titles)
            self.assertNotIn("Alcance del SGC", titles)

    def test_the_list_counts_attendees_and_actions_and_filters_by_year_and_state(self) -> None:
        current = self.review([self.ana, self.luis])
        self.action(current, fecha_limite=date(2026, 9, 30))
        self.action(current)
        self.action(current, estado="completada", fecha_completada=date(2026, 8, 1))
        older = self.review([self.eva], fecha=date(2025, 11, 5), periodo_desde=date(2025, 1, 1),
                            periodo_hasta=date(2025, 10, 31))
        self.advance(older, "hold")
        self.login(AUDITOR)
        html = self.html(f"{BASE}/")
        row = self.row(html, "revision", current)
        for text in ("10/07/2026", "01/04/2026 – 30/06/2026", "Borrador",
                     '<td class="num">2</td>', "2 / 1 / 3"):
            self.assertIn(text, row)
        for text in ("05/11/2025", "Celebrada", '<td class="num">1</td>', "0 / 0 / 0"):
            self.assertIn(text, self.row(html, "revision", older))
        cases = {"": {current, older}, "?anio=2025": {older}, "?estado=celebrada": {older},
                 "?anio=2026&estado=borrador": {current}, "?estado=desconocido": {current, older},
                 "?anio=2024": set()}
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(expected, self.listed(f"{BASE}/{query}", "revision"))
        self.assertIn("Ninguna revisión coincide con los filtros.",
                      self.html(f"{BASE}/?anio=2024"))
        self.assertIn('<option selected value="celebrada">Celebrada</option>',
                      self.html(f"{BASE}/?estado=celebrada"))


class RolesTestCase(ReviewScreensBase):
    def test_auditors_write_drafts_and_actions_but_never_hold_close_or_delete(self) -> None:
        rid = self.review([self.ana])
        aid = self.action(rid, fecha_limite=date(2026, 9, 30))
        empty = self.review(fecha=date(2025, 11, 5), periodo_desde=date(2025, 1, 1),
                            periodo_hasta=date(2025, 10, 31))
        self.login(AUDITOR)
        page = self.html(f"{BASE}/{rid}")
        self.assertNotIn(f'action="{BASE}/{rid}/celebrar"', page)
        self.assertIn(f'href="{BASE}/{rid}/editar"', page)
        for url in (f"{BASE}/{rid}/celebrar", f"{BASE}/{rid}/cerrar", f"{BASE}/{empty}/eliminar",
                    f"{BASE}/{rid}/acciones/{aid}/eliminar"):
            with self.subTest(url=url):
                self.denied("post", url)
        self.assertEqual(EstadoRevisionDireccion.borrador,
                         self.load(RevisionDireccion, rid).estado)
        self.assertIsNotNone(self.load(RevisionDireccion, empty))
        self.post(f"{BASE}/{rid}/editar", REVIEW_FORM | {"asistente_ids": [str(self.eva)]},
                  expect=f"{BASE}/{rid}")
        self.assertEqual([self.eva], self.load(RevisionDireccion, rid).asistente_ids)
        self.post(f"{BASE}/{rid}/acciones/{aid}/editar", ACTION_FORM | {
            "responsable_id": str(self.luis), "estado": "en_curso", "fecha_limite": "2026-09-30"},
            expect=f"{BASE}/{rid}")
        self.assertEqual(EstadoAccionRevision.en_curso, self.load(AccionRevision, aid).estado)
        self.assertIn(f'class="nav__link" href="{BASE}/"', self.html("/dashboard/"))

    def test_other_roles_are_refused_everywhere(self) -> None:
        rid = self.review([self.ana])
        aid = self.action(rid, fecha_limite=date(2026, 9, 30))
        self.login(OPERATIVO)
        review, action = f"{BASE}/{rid}", f"{BASE}/{rid}/acciones/{aid}"
        for method, url in (
                ("get", f"{BASE}/"), ("get", review),
                ("get", f"{BASE}/nueva"), ("post", f"{BASE}/nueva"),
                ("get", f"{review}/editar"), ("post", f"{review}/editar"),
                ("get", f"{review}/conclusiones"), ("post", f"{review}/conclusiones"),
                ("post", f"{review}/celebrar"), ("post", f"{review}/cerrar"),
                ("post", f"{review}/eliminar"), ("get", f"{review}/acciones/nueva"),
                ("post", f"{review}/acciones/nueva"), ("get", f"{action}/editar"),
                ("post", f"{action}/editar"), ("post", f"{action}/eliminar")):
            with self.subTest(method=method, url=url):
                self.denied(method, url)
        self.assertNotIn(f'href="{BASE}/"', self.html("/dashboard/"))
        self.assertEqual(1, self.count(AccionRevision))


class RefusalsTestCase(ReviewScreensBase):
    def test_a_refused_review_keeps_what_was_typed(self) -> None:
        self.login(AUDITOR)
        cases = (({"periodo_hasta": "2026-07-31"}, mr.PERIOD_AFTER_MEETING),
                 ({"periodo_desde": "2026-07-01"}, mr.PERIOD_ORDER), ({"fecha": ""}, None))
        for values, flashed in cases:
            with self.subTest(values=values):
                data = REVIEW_FORM | {"asistente_ids": [str(self.luis)],
                                      "comentario_mejora": "Digitalizar los registros"} | values
                html = self.rerendered(f"{BASE}/nueva", data)
                self.assertIn("Digitalizar los registros", html)
                self.assertIn(f'<option selected value="{self.luis}">Luis Gil</option>', html)
                self.assertIn(flashed or "field--invalid", html)
        self.assertEqual(0, self.count(RevisionDireccion))

    def test_a_refused_action_keeps_what_was_typed(self) -> None:
        rid = self.review([self.ana])
        self.login(AUDITOR)
        cases = (({"estado": "completada"}, mr.COMPLETION_REQUIRED),
                 ({"fecha_completada": "2026-10-01"}, mr.COMPLETION_ONLY_WHEN_DONE),
                 ({"estado": "completada", "fecha_completada": "2026-10-13"},
                  mr.COMPLETION_IN_FUTURE),
                 ({"fecha_limite": ""}, None))
        for values, flashed in cases:
            with self.subTest(values=values):
                data = ACTION_FORM | {"responsable_id": str(self.luis)} | values
                html = self.rerendered(f"{BASE}/{rid}/acciones/nueva", data)
                self.assertIn("Contratar un técnico de calidad", html)
                self.assertIn(f'<option selected value="{self.luis}">Luis Gil</option>', html)
                self.assertIn(flashed or "field--invalid", html)
        self.assertEqual(0, self.count(AccionRevision))

    def test_holding_and_closing_refusals_are_flashed_on_the_page(self) -> None:
        future = self.review([self.ana], fecha=date(2026, 10, 20))
        alone = self.review()
        held = self.review([self.ana], fecha=date(2026, 8, 1))
        self.advance(held, "hold")
        self.login(ADMIN)
        for rid, step, message in ((future, "celebrar", mr.MEETING_IN_FUTURE),
                                   (alone, "celebrar", mr.NO_ATTENDEES),
                                   (held, "celebrar", mr.NOT_DRAFT),
                                   (held, "cerrar", mr.CONCLUSIONS_REQUIRED),
                                   (future, "cerrar", mr.NOT_HELD)):
            with self.subTest(step=step, message=message):
                self.post(f"{BASE}/{rid}/{step}", expect=f"{BASE}/{rid}")
                self.assertIn(("danger", message), self.flashes())
        self.assertEqual(EstadoRevisionDireccion.borrador,
                         self.load(RevisionDireccion, future).estado)

    def test_deletes_and_closed_reviews_follow_the_service(self) -> None:
        rid = self.review([self.ana])
        aid = self.action(rid)
        held = self.review([self.ana], fecha=date(2026, 8, 1))
        self.advance(held, "hold")
        kept = self.action(held)
        self.login(ADMIN)
        self.post(f"{BASE}/{rid}/eliminar", expect=f"{BASE}/{rid}")
        self.assertIn(("danger", mr.HAS_ACTIONS), self.flashes())
        self.post(f"{BASE}/{held}/eliminar", expect=f"{BASE}/{held}")
        self.assertIn(("danger", mr.DELETE_NOT_DRAFT), self.flashes())
        for method, step in (("get", "editar"), ("post", "eliminar")):
            response = getattr(self.client, method)(f"{BASE}/{rid}/acciones/{kept}/{step}")
            self.assertEqual(404, response.status_code)
        self.assertEqual(404, self.client.get(f"{BASE}/999").status_code)
        self.post(f"{BASE}/{rid}/acciones/{aid}/eliminar", expect=f"{BASE}/{rid}")
        self.post(f"{BASE}/{rid}/eliminar", expect=f"{BASE}/")
        self.assertIsNone(self.load(RevisionDireccion, rid))

        self.advance(held, "close")
        self.post(f"{BASE}/{held}/acciones/{kept}/eliminar", expect=f"{BASE}/{held}")
        self.assertIn(("danger", mr.REVIEW_CLOSED_FOR_ACTIONS), self.flashes())
        for url in (f"{BASE}/{held}/acciones/nueva", f"{BASE}/{held}/conclusiones",
                    f"{BASE}/{held}/editar"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(f"{BASE}/{held}", response.headers["Location"])
        self.assertIn(("danger", mr.REVIEW_CLOSED), self.flashes())
        self.assertIsNotNone(self.load(AccionRevision, kept))


class CsrfTestCase(ReviewScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def test_every_post_form_needs_its_token(self) -> None:
        rid = self.review([self.ana])
        aid = self.action(rid)
        self.login(ADMIN)
        page = _PostForms()
        page.feed(self.html(f"{BASE}/{rid}"))
        actions = {form["action"]: form for form in page.forms}
        self.assertEqual({f"{BASE}/{rid}/celebrar", f"{BASE}/{rid}/acciones/{aid}/eliminar"},
                         set(actions))
        for action, form in actions.items():
            with self.subTest(action=action):
                self.assertTrue(form["fields"].get("csrf_token"))
                self.assertEqual(400, self.client.post(action).status_code)
        for url in (f"{BASE}/nueva", f"{BASE}/{rid}/editar", f"{BASE}/{rid}/conclusiones",
                    f"{BASE}/{rid}/cerrar", f"{BASE}/{rid}/eliminar",
                    f"{BASE}/{rid}/acciones/nueva", f"{BASE}/{rid}/acciones/{aid}/editar"):
            with self.subTest(url=url):
                self.assertEqual(400, self.client.post(url, data={"fecha": "x"}).status_code)
        self.post(f"{BASE}/{rid}/celebrar", actions[f"{BASE}/{rid}/celebrar"]["fields"],
                  expect=f"{BASE}/{rid}")
        self.assertEqual(EstadoRevisionDireccion.celebrada,
                         self.load(RevisionDireccion, rid).estado)
