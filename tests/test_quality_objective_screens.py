"""Quality objective screens and dashboard card (task QO-2 of ``quality-objectives``).

Through the web: an objective is created with its process, owner, frequency and
direction pickers; measurements are added with the recorder defaulting to the
person linked to the user and the date to ``local_today``; the list shows the
target, the latest value with a met / not met badge and an overdue badge; the
objective's page lists its measurements and embeds the trend chart's data as
JSON (drawn by ``static/js/objectives.js``); the dashboard card lists the
active objectives off target or with an overdue measurement. Closing an
objective leaves its measurements read-only, and reopening it makes them
editable again. Every role reads (decision QO4); administrators and auditors
write; administrators delete. A refused write re-renders the form with the
input kept. Every test runs with the audit flush guard.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM, ScreensBase
from test_delete_forms import _PostForms

from app.extensions import db
from app.models import EstadoObjetivo, MedicionObjetivo, ObjetivoCalidad, RoleEnum
from app.services import process_operations, quality_objectives

BASE = "/objetivos-calidad"
TODAY = date(2026, 10, 11)
ROUTES = "app.routes.quality_objective_routes"
DATA_SCRIPT = re.compile(
    r'<script type="application/json" id="objective-data">(.*?)</script>', re.S)
FORM = {
    "titulo": "Entregas a tiempo",
    "descripcion": "Pedidos servidos en el plazo pactado",
    "proceso_id": "",
    "indicador": "% de pedidos entregados a tiempo",
    "unidad": "%",
    "meta": "95",
    "sentido": "mayor_igual",
    "valor_inicial": "88,5",
    "responsable_id": "",
    "anio": "",
    "fecha_inicio": "2026-01-01",
    "fecha_limite": "2026-12-31",
    "frecuencia": "mensual",
    "recursos": "Un turno de reparto adicional",
    "evaluacion": "Revisión mensual en el comité de calidad",
}


class ObjectiveScreensBase(ScreensBase):
    """``ScreensBase`` with today pinned in the objective and dashboard routes."""

    def setUp(self) -> None:
        super().setUp()
        for target in (f"{ROUTES}.local_today", "app.routes.dashboard_routes.local_today"):
            patcher = patch(target, return_value=TODAY)
            patcher.start()
            self.addCleanup(patcher.stop)

    def process(self, nombre: str = "Compras") -> int:
        with self.app.app_context():
            found = process_operations.create(db.session, SYSTEM, {"proceso": nombre})
            db.session.commit()
            return found.id_proceso

    def objective(self, **values) -> int:
        """An objective owned by Eva, created through the service."""
        payload = {"titulo": "Reclamaciones", "indicador": "Reclamaciones al mes",
                   "meta": 2, "sentido": "menor_igual", "responsable_id": self.eva,
                   "fecha_inicio": date(2026, 1, 1), "fecha_limite": date(2026, 12, 31),
                   "frecuencia": "mensual"} | values
        with self.app.app_context():
            found = quality_objectives.objectives.create(db.session, SYSTEM, payload)
            db.session.commit()
            return found.id

    def measure(self, objective_id: int, fecha: date, valor, **values) -> int:
        with self.app.app_context():
            found = quality_objectives.measurements.create(db.session, SYSTEM, {
                "objetivo_id": objective_id, "fecha": fecha, "valor": valor} | values,
                today=TODAY)
            db.session.commit()
            return found.id

    def close(self, objective_id: int, estado: str = "alcanzado") -> None:
        with self.app.app_context():
            quality_objectives.objectives.update(db.session, SYSTEM, objective_id,
                                                 {"estado": estado})
            db.session.commit()

    def create(self, **values) -> int:
        """Create an objective through the web form; returns its id."""
        response = self.post(f"{BASE}/nuevo", FORM | {"responsable_id": str(self.eva)} | values)
        match = re.fullmatch(rf"{BASE}/(\d+)", response.headers["Location"])
        self.assertIsNotNone(match, response.headers["Location"])
        return int(match.group(1))

    def add(self, objective_id: int, fecha: str, valor: str, **values) -> None:
        """Add a measurement through the web form."""
        self.post(f"{BASE}/{objective_id}/mediciones/nueva",
                  {"fecha": fecha, "valor": valor, "comentario": "",
                   "registrado_por_id": str(self.ana)} | values,
                  expect=f"{BASE}/{objective_id}")

    def chart(self, objective_id: int) -> dict:
        match = DATA_SCRIPT.search(self.html(f"{BASE}/{objective_id}"))
        self.assertIsNotNone(match, "the objective's chart data is missing")
        return json.loads(match.group(1))

    def card(self) -> str:
        html = self.html("/dashboard/")
        match = re.search(r'<article class="card" id="objetivos-calidad">(.*?)</article>',
                          html, re.S)
        self.assertIsNotNone(match, "the dashboard card is missing")
        return match.group(1)

    def measurement_values(self, objective_id: int) -> list[Decimal]:
        with self.app.app_context():
            return [m.valor for m in db.session.query(MedicionObjetivo)
                    .filter_by(objetivo_id=objective_id).order_by(MedicionObjetivo.fecha)]

    def measurement_ids(self, objective_id: int) -> list[int]:
        with self.app.app_context():
            return [m.id for m in db.session.query(MedicionObjetivo)
                    .filter_by(objetivo_id=objective_id).order_by(MedicionObjetivo.fecha)]


class WebFlowTestCase(ObjectiveScreensBase):
    def test_create_measure_list_chart_dashboard_close_and_reopen(self) -> None:
        process_id = self.process("Logística")
        self.login(ADMIN)

        # The form offers the process, owner, frequency and direction pickers.
        html = self.html(f"{BASE}/nuevo")
        for marker in ('name="proceso_id"', 'name="responsable_id"', 'name="frecuencia"',
                       'name="sentido"', ">Logística</option>", ">Eva Ruiz</option>",
                       ">Trimestral</option>", "≥"):
            self.assertIn(marker, html)

        oid = self.create(proceso_id=str(process_id))
        with self.app.app_context():
            stored = db.session.get(ObjetivoCalidad, oid)
            self.assertEqual((2026, EstadoObjetivo.activo, self.eva, process_id),
                             (stored.anio, stored.estado, stored.responsable_id,
                              stored.proceso_id))
            self.assertEqual(Decimal("88.5"), stored.valor_inicial)
        page = self.html(f"{BASE}/{oid}")
        for text in ("Entregas a tiempo", "Logística", "Eva Ruiz", "≥ 95 %", "88,5 %",
                     "Un turno de reparto adicional",
                     "Revisión mensual en el comité de calidad", "Sin mediciones"):
            self.assertIn(text, page)
        self.assertIsNone(DATA_SCRIPT.search(page), "no chart without measurements")

        # The measurement form starts on today and on the user's own person.
        html = self.html(f"{BASE}/{oid}/mediciones/nueva")
        self.assertIn('value="2026-10-11"', html)
        self.assertIn(f'<option selected value="{self.ana}">Ana Pérez</option>', html)
        self.add(oid, "2026-08-31", "90", comentario="Huelga de transporte")
        self.add(oid, "2026-09-30", "96,25")
        self.assertEqual([Decimal("90"), Decimal("96.25")], self.measurement_values(oid))

        page = self.html(f"{BASE}/{oid}")
        for text in ("31/08/2026", "30/09/2026", "Huelga de transporte", "96,25 %",
                     'badge--danger">No cumple', 'badge--ok">Cumple', "Ana Pérez", "Sube"):
            self.assertIn(text, page)
        self.assertIn('src="/static/js/objectives.js"', page)
        self.assertIn('src="/static/lib/chart.umd.min.js"', page)
        self.assertEqual({
            "fechas": ["2026-08-31", "2026-09-30"], "valores": [90.0, 96.25],
            "cumple": [False, True], "meta": 95.0, "sentido": "≥", "unidad": "%",
            "indicador": "% de pedidos entregados a tiempo",
        }, self.chart(oid))

        # A second objective, off target and with an overdue measurement.
        other = self.create(titulo="Reclamaciones", indicador="Reclamaciones al mes", meta="2",
                            sentido="menor_igual", unidad="", valor_inicial="")
        self.add(other, "2026-08-31", "5")
        rows = {title: row for title, row in re.findall(
            r'<tr>\s*<td><a href="/objetivos-calidad/\d+">([^<]+)</a>(.*?)</tr>',
            self.html(f"{BASE}/"), re.S)}
        self.assertEqual({"Entregas a tiempo", "Reclamaciones"}, set(rows))
        for text in ("≥ 95 %", "96,25 %", 'badge--ok">Cumple', "Eva Ruiz", "31/12/2026",
                     "Activo"):
            self.assertIn(text, rows["Entregas a tiempo"])
        self.assertNotIn("Medición vencida", rows["Entregas a tiempo"])
        for text in ("≤ 2", 'badge--danger">No cumple', "Medición vencida"):
            self.assertIn(text, rows["Reclamaciones"])

        card = self.card()
        self.assertIn(f'href="{BASE}/{other}">Reclamaciones</a>', card)
        self.assertIn("No cumple la meta", card)
        self.assertIn("Medición vencida", card)
        self.assertNotIn("Entregas a tiempo", card)

        # Closing it: the card forgets it and its measurements turn read-only.
        self.post(f"{BASE}/{other}/estado/no_alcanzado", expect=f"{BASE}/{other}")
        self.assertEqual(EstadoObjetivo.no_alcanzado, self.load(ObjetivoCalidad, other).estado)
        self.assertNotIn("Reclamaciones", self.card())
        self.assertIn("Ningún objetivo activo", self.card())
        page = self.html(f"{BASE}/{other}")
        self.assertIn("No alcanzado", page)
        (mid,) = self.measurement_ids(other)
        for marker in (f'href="{BASE}/{other}/mediciones/nueva"',
                       f'href="{BASE}/{other}/mediciones/{mid}/editar"',
                       f'action="{BASE}/{other}/mediciones/{mid}/eliminar"'):
            self.assertNotIn(marker, page)
        self.assertIn(f'action="{BASE}/{other}/estado/activo"', page)
        self.assertEqual(302, self.client.get(f"{BASE}/{other}/mediciones/nueva").status_code)
        for url, data in ((f"{BASE}/{other}/mediciones/nueva",
                           {"fecha": "2026-09-30", "valor": "1"}),
                          (f"{BASE}/{other}/mediciones/{mid}/editar",
                           {"fecha": "2026-08-31", "valor": "1"}),
                          (f"{BASE}/{other}/mediciones/{mid}/eliminar", {})):
            with self.subTest(url=url):
                self.post(url, data, expect=f"{BASE}/{other}")
                self.assertIn(("danger", quality_objectives.OBJECTIVE_NOT_ACTIVE),
                              self.flashes())
        self.assertEqual([Decimal("5")], self.measurement_values(other))

        # Reopening it makes the measurements editable again.
        self.post(f"{BASE}/{other}/estado/activo", expect=f"{BASE}/{other}")
        self.assertIn(f'href="{BASE}/{other}/mediciones/nueva"', self.html(f"{BASE}/{other}"))
        self.post(f"{BASE}/{other}/mediciones/{mid}/editar",
                  {"fecha": "2026-08-31", "valor": "1,5", "comentario": "Corregida",
                   "registrado_por_id": ""}, expect=f"{BASE}/{other}")
        self.assertEqual([Decimal("1.5")], self.measurement_values(other))

    def test_the_target_is_shown_locked_once_measured(self) -> None:
        oid = self.objective()
        self.login(ADMIN)
        html = self.html(f"{BASE}/{oid}/editar")
        self.assertIn('name="meta"', html)
        self.assertIn('name="sentido"', html)
        self.measure(oid, date(2026, 9, 30), 1)
        html = self.html(f"{BASE}/{oid}/editar")
        self.assertNotIn('name="meta"', html)
        self.assertNotIn('name="sentido"', html)
        self.assertRegex(html, r'id="meta-fija"[^>]*value="2"[^>]*disabled')
        self.assertIn("ya tiene mediciones", html)
        edit = {key: value for key, value in FORM.items()} | {
            "titulo": "Menos reclamaciones", "indicador": "Reclamaciones al mes",
            "responsable_id": str(self.eva), "meta": "50", "sentido": "mayor_igual",
            "valor_inicial": "", "anio": "2026"}
        self.post(f"{BASE}/{oid}/editar", edit, expect=f"{BASE}/{oid}")
        stored = self.load(ObjetivoCalidad, oid)
        self.assertEqual(("Menos reclamaciones", Decimal("2")), (stored.titulo, stored.meta))
        self.assertEqual("menor_igual", stored.sentido.name)

    def test_a_measurement_under_another_objective_is_not_found(self) -> None:
        first, second = self.objective(), self.objective(titulo="Otro")
        mid = self.measure(first, date(2026, 9, 30), 1)
        self.login(ADMIN)
        self.assertEqual(404, self.client.get(f"{BASE}/{second}/mediciones/{mid}/editar")
                         .status_code)
        self.assertEqual(404, self.client.post(f"{BASE}/{second}/mediciones/{mid}/eliminar")
                         .status_code)
        self.assertEqual(404, self.client.get(f"{BASE}/999").status_code)

    def test_deletes_follow_the_service(self) -> None:
        measured, empty = self.objective(), self.objective(titulo="Sin medir")
        mid = self.measure(measured, date(2026, 9, 30), 1)
        self.login(ADMIN)
        self.post(f"{BASE}/{measured}/eliminar", expect=f"{BASE}/{measured}")
        self.assertIn(("danger", quality_objectives.HAS_MEASUREMENTS), self.flashes())
        self.post(f"{BASE}/{measured}/mediciones/{mid}/eliminar", expect=f"{BASE}/{measured}")
        self.assertEqual([], self.measurement_values(measured))
        self.post(f"{BASE}/{empty}/eliminar", expect=f"{BASE}/")
        self.assertIsNone(self.load(ObjetivoCalidad, empty))

    def test_the_list_filters_by_year_state_owner_and_process(self) -> None:
        process_id = self.process("Ventas")
        self.objective(titulo="Objetivo A", proceso_id=process_id)
        self.objective(titulo="Objetivo B", responsable_id=self.luis,
                       fecha_inicio=date(2025, 1, 1), fecha_limite=date(2025, 12, 31))
        closed = self.objective(titulo="Objetivo C")
        self.close(closed, "cancelado")
        self.login(OPERATIVO)
        cases = {
            "": {"Objetivo A", "Objetivo B", "Objetivo C"},
            "?anio=2025": {"Objetivo B"},
            "?estado=cancelado": {"Objetivo C"},
            f"?responsable_id={self.luis}": {"Objetivo B"},
            f"?proceso_id={process_id}": {"Objetivo A"},
            f"?anio=2026&estado=activo&responsable_id={self.eva}": {"Objetivo A"},
            "?estado=desconocido": {"Objetivo A", "Objetivo B", "Objetivo C"},
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                html = self.html(f"{BASE}/{query}")
                self.assertEqual(expected, set(re.findall(
                    r'<td><a href="/objetivos-calidad/\d+">([^<]+)</a>', html)))
        html = self.html(f"{BASE}/?anio=2026&estado=activo")
        self.assertIn("2 activos", html)
        html = self.html(f"{BASE}/?proceso_id={process_id}")
        self.assertIn(f'<option selected value="{process_id}">Ventas</option>', html)


class RolesTestCase(ObjectiveScreensBase):
    def test_every_role_reads_writers_write_and_administrators_delete(self) -> None:
        oid = self.objective()
        mid = self.measure(oid, date(2026, 9, 30), 5)
        empty = self.objective(titulo="Sin medir")
        for role in RoleEnum:
            with self.subTest(role=role.name):
                self.login(role)
                self.assertIn("Reclamaciones", self.html(f"{BASE}/"))
                self.assertIn("Reclamaciones", self.html(f"{BASE}/{oid}"))
                self.assertIn("Reclamaciones", self.card())
        self.assertIn(f'class="nav__link" href="{BASE}/"', self.html("/dashboard/"))

        self.login(OPERATIVO)
        for method, url in (("get", f"{BASE}/nuevo"), ("post", f"{BASE}/nuevo"),
                            ("get", f"{BASE}/{oid}/editar"), ("post", f"{BASE}/{oid}/editar"),
                            ("post", f"{BASE}/{oid}/estado/alcanzado"),
                            ("get", f"{BASE}/{oid}/mediciones/nueva"),
                            ("post", f"{BASE}/{oid}/mediciones/{mid}/editar"),
                            ("post", f"{BASE}/{oid}/mediciones/{mid}/eliminar"),
                            ("post", f"{BASE}/{empty}/eliminar")):
            with self.subTest(role="OPERATIVO", url=url):
                self.denied(method, url)

        self.login(AUDITOR)
        for url in (f"{BASE}/{oid}/mediciones/{mid}/eliminar", f"{BASE}/{empty}/eliminar"):
            with self.subTest(role="AUDITOR", url=url):
                self.denied("post", url)
        self.add(oid, "2026-10-10", "1")
        self.post(f"{BASE}/{oid}/estado/alcanzado", expect=f"{BASE}/{oid}")
        self.assertEqual(EstadoObjetivo.alcanzado, self.load(ObjetivoCalidad, oid).estado)
        self.assertIsNotNone(self.load(ObjetivoCalidad, empty))


class InputKeptTestCase(ObjectiveScreensBase):
    def test_a_refused_objective_keeps_what_was_typed(self) -> None:
        self.login(AUDITOR)
        cases = (
            ({"fecha_limite": "2025-12-31"}, quality_objectives.DEADLINE_BEFORE_START),
            ({"meta": ""}, None),
            ({"meta": "noventa"}, None),
        )
        for values, flashed in cases:
            with self.subTest(values=values):
                data = FORM | {"responsable_id": str(self.eva), "titulo": "Plazos 2026"} | values
                html = self.rerendered(f"{BASE}/nuevo", data)
                self.assertIn('value="Plazos 2026"', html)
                self.assertIn('value="88,5"', html)
                self.assertIn("Revisión mensual en el comité de calidad", html)
                if flashed:
                    self.assertIn(flashed, html)
                else:
                    self.assertIn("field--invalid", html)
        self.assertEqual(0, self.count(ObjetivoCalidad))

    def test_a_refused_measurement_keeps_what_was_typed(self) -> None:
        oid = self.objective()
        mid = self.measure(oid, date(2026, 9, 30), 1)
        self.login(AUDITOR)
        cases = (
            (f"{BASE}/{oid}/mediciones/nueva", {"fecha": "2026-10-12"},
             quality_objectives.IN_THE_FUTURE),
            (f"{BASE}/{oid}/mediciones/nueva", {"fecha": "2025-12-31"}, "periodo del objetivo"),
            (f"{BASE}/{oid}/mediciones/nueva", {"fecha": "2026-09-30"},
             quality_objectives.DUPLICATE_DATE),
            (f"{BASE}/{oid}/mediciones/nueva", {"valor": "mucho"}, None),
            (f"{BASE}/{oid}/mediciones/{mid}/editar", {"fecha": "2026-10-12"},
             quality_objectives.IN_THE_FUTURE),
        )
        for url, values, flashed in cases:
            with self.subTest(url=url, values=values):
                data = {"fecha": "2026-10-01", "valor": "3,5", "comentario": "Tres quejas",
                        "registrado_por_id": str(self.luis)} | values
                html = self.rerendered(url, data)
                self.assertIn(f'value="{data["valor"]}"', html)
                self.assertIn("Tres quejas", html)
                self.assertIn(f'<option selected value="{self.luis}">Luis Gil</option>', html)
                if flashed:
                    self.assertIn(flashed, html)
                else:
                    self.assertIn("field--invalid", html)
        self.assertEqual([Decimal("1")], self.measurement_values(oid))


class CsrfTestCase(ObjectiveScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def test_every_post_form_needs_its_token(self) -> None:
        oid = self.objective()
        mid = self.measure(oid, date(2026, 9, 30), 1)
        self.login(ADMIN)
        page = _PostForms()
        page.feed(self.html(f"{BASE}/{oid}"))
        actions = {form["action"]: form for form in page.forms}
        self.assertEqual({f"{BASE}/{oid}/estado/{estado}" for estado in
                          ("alcanzado", "no_alcanzado", "cancelado")}
                         | {f"{BASE}/{oid}/mediciones/{mid}/eliminar"}, set(actions))
        for action, form in actions.items():
            with self.subTest(action=action):
                self.assertTrue(form["fields"].get("csrf_token"))
                self.assertEqual(400, self.client.post(action).status_code)
        for url in (f"{BASE}/nuevo", f"{BASE}/{oid}/editar", f"{BASE}/{oid}/mediciones/nueva",
                    f"{BASE}/{oid}/mediciones/{mid}/editar", f"{BASE}/{oid}/eliminar"):
            with self.subTest(url=url):
                self.assertEqual(400, self.client.post(url, data={"titulo": "x"}).status_code)
        self.post(f"{BASE}/{oid}/estado/alcanzado",
                  actions[f"{BASE}/{oid}/estado/alcanzado"]["fields"], expect=f"{BASE}/{oid}")
        self.assertEqual(EstadoObjetivo.alcanzado, self.load(ObjetivoCalidad, oid).estado)
