"""External provider screens and dashboard card (EP-2 of ``external-providers``).

Through the web (ISO 9001 clause 8.4): a provider is created ``pendiente`` with
its supply, processes and selection criteria; an administrator approves,
conditions and blocks it, each with a reason (required to condition or block),
and its page shows the current decision with its date, decider and reason.
Administrators and auditors record scored evaluations; the list and the page
show the latest result with its average. A nonconformity with the ``proveedor``
origin names the provider, another origin is refused, and both pages link each
other (tests/test_nonconformity_routes.py). The dashboard card links what
``providers.attention`` flags to the filtered list (tests/test_dashboard.py).
Every role reads; only administrators decide and delete (EP2, EP5). A refused
write re-renders the form with the input kept. Every test runs with the audit
flush guard.
"""

from __future__ import annotations

import re
from datetime import date
from unittest.mock import patch

from test_audit_screens import ADMIN, AUDITOR, OPERATIVO, SYSTEM
from test_delete_forms import _PostForms
from test_process_role_screens import RegisterScreensBase

from app.extensions import db
from app.models import EstadoProveedor, EvaluacionProveedor, Person, Proveedor, RoleEnum
from app.services import nonconformities, providers

BASE = "/proveedores"
TODAY = date(2026, 10, 18)
GOOD = {"calidad": 4, "plazo": 4, "servicio": 3, "documentacion": 5}  # 4.00, apto
BAD = {"calidad": 2, "plazo": 2, "servicio": 2, "documentacion": 3}  # 2.25, no_apto
BAD_FORM = {key: str(value) for key, value in BAD.items()}
DECISIONS = {"aprobar", "condicionar", "bloquear"}
PROVIDER_FORM = {
    "nombre": "Aceros del Norte", "nif": "b-1234 5678", "contacto": "Marta Gil",
    "email": "Compras@Aceros.example", "telefono": "944 000 000", "tipo_suministro": "producto",
    "descripcion_suministro": "Chapa de acero laminado",
    "criterios_seleccion": "Certificado ISO 9001 vigente", "fecha_proxima_evaluacion": "",
    "notas": "Proveedor histórico",
}
EVALUATION_FORM = {"fecha": "2026-10-10", "calidad": "4", "plazo": "4", "servicio": "3",
                   "documentacion": "5", "comentario": "Entregas puntuales",
                   "fecha_proxima": "2027-04-10"}


class ProviderScreensBase(RegisterScreensBase):
    """``RegisterScreensBase`` with today pinned and provider helpers."""

    def setUp(self) -> None:
        super().setUp()
        for target in ("app.routes.provider_routes.local_today",
                       "app.routes.dashboard_routes.local_today"):
            patcher = patch(target, return_value=TODAY)
            patcher.start()
            self.addCleanup(patcher.stop)

    def provider(self, nombre: str = "Aceros del Norte", **values) -> int:
        with self.app.app_context():
            found = providers.providers.create(db.session, SYSTEM, {
                "nombre": nombre, "tipo_suministro": "producto"} | values)
            db.session.commit()
            return found.id

    def decide(self, provider_id: int, decision: str, motivo: str = "Motivo") -> None:
        with self.app.app_context():
            if decision == "approve":
                providers.approve(db.session, SYSTEM, provider_id, today=TODAY, motivo=motivo)
            else:
                getattr(providers, decision)(db.session, SYSTEM, provider_id, motivo, today=TODAY)
            db.session.commit()

    def evaluate(self, provider_id: int, scores: dict | None = None,
                 fecha: date = date(2026, 10, 10), **values) -> int:
        with self.app.app_context():
            found = providers.evaluations.create(db.session, SYSTEM, {
                "proveedor_id": provider_id, "fecha": fecha, "evaluador_id": self.eva,
                **(scores or GOOD), **values}, today=TODAY)
            db.session.commit()
            return found.id

    def nonconformity(self, provider_id: int | None, origen: str = "proveedor") -> int:
        with self.app.app_context():
            found = nonconformities.create(db.session, SYSTEM, {
                "descripcion": "Lote con rebabas", "fecha_detectada": date(2026, 10, 12),
                "origen": origen, "proveedor_id": provider_id})
            db.session.commit()
            return found.id

    def row(self, url: str, record_id: int) -> str:
        return " ".join(self.rows(url)[record_id])

    @staticmethod
    def decisions(html: str) -> set[str]:
        """The decision forms a provider's page offers."""
        return set(re.findall(rf'action="{BASE}/\d+/(aprobar|condicionar|bloquear)"', html))

    def state(self, provider_id: int) -> str:
        return self.text(self.block(self.html(f"{BASE}/{provider_id}"), "estado"))


class ProviderFlowTestCase(ProviderScreensBase):
    def test_a_provider_is_approved_evaluated_conditioned_and_blocked(self) -> None:
        compras = self.process("Compras")
        with self.app.app_context():  # core insert: an inactive person is never offered
            db.session.execute(Person.__table__.insert().values(nombre="Pablo Baja",
                                                                activo=False))
            db.session.commit()
        self.login(AUDITOR)
        form = self.html(f"{BASE}/nuevo")
        for marker in ('name="tipo_suministro"', ">Proceso externalizado</option>",
                       ">Compras</option>", 'name="criterios_seleccion"'):
            self.assertIn(marker, form)
        self.assertIn("multiple", self.select(form, "procesos"))
        pid = self.created(BASE, PROVIDER_FORM | {"procesos": [str(compras)]})
        self.assertIn(("success", "Proveedor «Aceros del Norte» creado."), self.flashes())
        page = self.html(f"{BASE}/{pid}")
        self.assertIn("Pendiente", self.text(self.block(page, "estado")))
        for marker in ("B12345678", "Marta Gil", "compras@aceros.example", "944 000 000",
                       "Producto"):
            self.assertIn(marker, self.block(page, "identificacion"))
        for marker in ("Chapa de acero laminado", "Certificado ISO 9001 vigente",
                       "Proveedor histórico"):
            self.assertIn(marker, page)
        self.assertIn(f'href="/procesos/{compras}">Compras</a>', self.block(page, "procesos"))
        self.assertEqual(set(), self.decisions(page))

        self.login(ADMIN)
        self.assertEqual(DECISIONS, self.decisions(self.html(f"{BASE}/{pid}")))
        self.post(f"{BASE}/{pid}/aprobar", {"motivo": "Homologado tras la auditoría"},
                  expect=f"{BASE}/{pid}")
        self.assertIn(("success", "Proveedor aprobado."), self.flashes())
        page = self.html(f"{BASE}/{pid}")
        for marker in ("Aprobado", "18/10/2026", "Ana Pérez", "Homologado tras la auditoría"):
            self.assertIn(marker, self.text(self.block(page, "estado")))
        self.assertEqual({"condicionar", "bloquear"}, self.decisions(page))

        self.login(AUDITOR)
        form = self.html(f"{BASE}/{pid}/evaluaciones/nueva")
        evaluators = self.select(form, "evaluador_id")
        self.assertIn(f'value="{self.eva}">Eva Ruiz</option>', evaluators)
        self.assertNotIn("Pablo Baja", evaluators)
        self.assertIn('value="2026-10-18"', form)
        self.assertIn('<option value="5">5</option>', self.select(form, "calidad"))
        self.post(f"{BASE}/{pid}/evaluaciones/nueva",
                  EVALUATION_FORM | {"evaluador_id": str(self.eva)}, expect=f"{BASE}/{pid}")
        self.assertIn(("success", "Evaluación registrada."), self.flashes())
        row = self.row(f"{BASE}/", pid)
        for marker in ("Aceros del Norte", "Producto", "Aprobado", "Apto 4,00", "10/04/2027",
                       "0 / 0"):
            self.assertIn(marker, row)
        page = self.html(f"{BASE}/{pid}")
        (evaluation,) = re.findall(r'<tr id="evaluacion-\d+">(.*?)</tr>', page, re.S)
        cells = [self.text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", evaluation, re.S)]
        self.assertEqual(["10/10/2026", "Eva Ruiz", "4", "4", "3", "5", "4,00", "Apto",
                          "Entregas puntuales"], cells[:9])
        for marker in ("Apto", "4,00", "1"):
            self.assertIn(marker, self.text(self.block(page, "desempeno")))
        self.assertIn("10/04/2027", self.text(self.block(page, "identificacion")))

        self.login(ADMIN)
        self.post(f"{BASE}/{pid}/condicionar", {"motivo": "  "}, expect=f"{BASE}/{pid}")
        self.assertIn(("danger", providers.REASON_REQUIRED), self.flashes())
        self.assertIn("Aprobado", self.state(pid))
        self.post(f"{BASE}/{pid}/condicionar", {"motivo": "Retrasos en septiembre"},
                  expect=f"{BASE}/{pid}")
        self.assertIn("Retrasos en septiembre", self.state(pid))
        self.assertEqual({"aprobar", "bloquear"}, self.decisions(self.html(f"{BASE}/{pid}")))
        self.post(f"{BASE}/{pid}/bloquear", {"motivo": "Lote rechazado"}, expect=f"{BASE}/{pid}")
        self.assertIn(("success", "Proveedor bloqueado."), self.flashes())
        for marker in ("Bloqueado", "Lote rechazado"):
            self.assertIn(marker, self.state(pid))
        self.assertEqual({"aprobar", "condicionar"}, self.decisions(self.html(f"{BASE}/{pid}")))
        self.assertIn("Bloqueado", self.row(f"{BASE}/", pid))

    def test_evaluations_are_edited_and_deleted_from_the_providers_page(self) -> None:
        pid = self.provider()
        eid = self.evaluate(pid)
        self.login(AUDITOR)
        form = self.html(f"{BASE}/{pid}/evaluaciones/{eid}/editar")
        self.assertIn('<option selected value="3">3</option>', self.select(form, "servicio"))
        self.post(f"{BASE}/{pid}/evaluaciones/{eid}/editar",
                  EVALUATION_FORM | {"evaluador_id": str(self.eva)} | BAD_FORM,
                  expect=f"{BASE}/{pid}")
        self.assertIn(("success", "Evaluación actualizada."), self.flashes())
        self.assertIn("No apto 2,25", self.row(f"{BASE}/", pid))
        self.login(ADMIN)
        self.post(f"{BASE}/{pid}/evaluaciones/{eid}/eliminar", expect=f"{BASE}/{pid}")
        self.assertIn(("success", "Evaluación eliminada."), self.flashes())
        self.assertEqual(0, self.count(EvaluacionProveedor))
        self.assertIn("Sin evaluar", self.row(f"{BASE}/", pid))
        self.post(f"{BASE}/{pid}/eliminar", expect=f"{BASE}/")
        self.assertIn(("success", "Proveedor eliminado."), self.flashes())
        self.assertIsNone(self.load(Proveedor, pid))



class ListTestCase(ProviderScreensBase):
    def test_the_list_filters_by_state_supply_process_result_overdue_and_name(self) -> None:
        compras = self.process("Compras")
        good = self.provider("Aceros del Norte", procesos=[compras])
        self.evaluate(good)
        self.decide(good, "approve")
        bad = self.provider("Transportes Rápidos", tipo_suministro="servicio")
        self.evaluate(bad, BAD)
        late = self.provider("Calibraciones Sur", tipo_suministro="proceso_externalizado",
                             fecha_proxima_evaluacion=date(2026, 9, 30))
        everyone = {good, bad, late}
        self.login(OPERATIVO)
        cases = {"": everyone, "?estado=aprobado": {good}, "?estado=pendiente": {bad, late},
                 "?tipo_suministro=servicio": {bad}, f"?proceso_id={compras}": {good},
                 "?ultimo_resultado=apto": {good}, "?ultimo_resultado=no_apto": {bad},
                 "?evaluacion_vencida=1": {late}, "?evaluacion_vencida=0": {good, bad},
                 "?con_procesos=1": {good}, "?nombre=NORTE": {good},
                 "?estado=aprobado&nombre=sur": set(),
                 "?estado=nada&tipo_suministro=nada&ultimo_resultado=nada"
                 "&evaluacion_vencida=quizas&proceso_id=999": everyone}
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(expected, set(self.rows(f"{BASE}/{query}")))
        html = self.html(f"{BASE}/?estado=aprobado&nombre=sur")
        self.assertIn('<option selected value="aprobado">Aprobado</option>', html)
        self.assertIn('value="sur"', html)
        self.assertIn("2 activos", html)
        self.assertIn("Ningún proveedor coincide con los filtros.", html)
        self.assertIn("No apto 2,25", self.row(f"{BASE}/", bad))
        late_row = self.row(f"{BASE}/", late)
        for marker in ("Proceso externalizado", "Pendiente", "Sin evaluar", "30/09/2026",
                       "Evaluación vencida"):
            self.assertIn(marker, late_row)
        self.assertNotIn("Evaluación vencida", self.row(f"{BASE}/", good))


class AccessTestCase(ProviderScreensBase):
    def test_every_role_reads_auditors_write_and_only_administrators_decide(self) -> None:
        pid = self.provider()
        eid = self.evaluate(pid)
        empty = self.provider("Sin evaluaciones")
        for role in RoleEnum:
            self.login(role)
            for url in (f"{BASE}/", f"{BASE}/{pid}"):
                with self.subTest(role=role.name, url=url):
                    self.html(url)
        forms = [f"{BASE}/nuevo", f"{BASE}/{pid}/editar", f"{BASE}/{pid}/evaluaciones/nueva",
                 f"{BASE}/{pid}/evaluaciones/{eid}/editar"]
        admin_only = [f"{BASE}/{empty}/eliminar", f"{BASE}/{pid}/evaluaciones/{eid}/eliminar",
                      *(f"{BASE}/{pid}/{decision}" for decision in sorted(DECISIONS))]
        self.login(OPERATIVO)
        for method, url in [*((m, u) for u in forms for m in ("get", "post")),
                            *(("post", u) for u in admin_only)]:
            with self.subTest(role="OPERATIVO", method=method, url=url):
                self.denied(method, url)
        self.login(AUDITOR)
        for url in forms:
            with self.subTest(role="AUDITOR", url=url):
                self.html(url)
        for url in admin_only:
            with self.subTest(role="AUDITOR", url=url):
                self.denied("post", url)
        self.assertEqual(EstadoProveedor.pendiente, self.load(Proveedor, pid).estado)
        self.assertEqual((2, 1), (self.count(Proveedor), self.count(EvaluacionProveedor)))

    def test_refused_deletes_flash_why_and_unknown_records_are_404(self) -> None:
        pid = self.provider()
        eid = self.evaluate(pid)
        cited = self.provider("Citado")
        self.nonconformity(cited)
        self.login(ADMIN)
        for record, message in ((pid, providers.HAS_EVALUATIONS),
                                (cited, providers.HAS_NONCONFORMITIES)):
            with self.subTest(message=message):
                self.post(f"{BASE}/{record}/eliminar", expect=f"{BASE}/{record}")
                self.assertIn(("danger", message), self.flashes())
        self.assertEqual(2, self.count(Proveedor))
        for url in (f"{BASE}/999", f"{BASE}/999/editar", f"{BASE}/999/evaluaciones/nueva",
                    f"{BASE}/{cited}/evaluaciones/{eid}/editar"):
            with self.subTest(url=url):
                self.assertEqual(404, self.client.get(url).status_code)
        self.assertEqual(404, self.client.post(
            f"{BASE}/{cited}/evaluaciones/{eid}/eliminar").status_code)
        self.assertEqual(1, self.count(EvaluacionProveedor))

    def test_the_navigation_links_the_register_with_its_clause(self) -> None:
        for role in RoleEnum:
            self.login(role)
            with self.subTest(role=role.name):
                self.assertRegex(self.html("/dashboard/"),
                                 rf'class="nav__link" href="{BASE}/"\s*>\s*Proveedores'
                                 r'<span class="nav__code">8.4</span>')
        self.assertRegex(self.html(f"{BASE}/"), rf'href="{BASE}/"\s+aria-current="page"')


class FormErrorTestCase(ProviderScreensBase):
    def test_refused_writes_re_render_the_form_with_the_input(self) -> None:
        compras = self.process("Compras")
        self.provider("Aceros del Norte")
        self.login(AUDITOR)
        html = self.rerendered(f"{BASE}/nuevo", PROVIDER_FORM | {"procesos": [str(compras)]})
        self.assertIn(providers.NAME_TAKEN, html)
        self.assertIn("Certificado ISO 9001 vigente", html)
        self.assertIn(f'<option selected value="{compras}">Compras</option>', html)
        html = self.rerendered(f"{BASE}/nuevo", PROVIDER_FORM | {"nombre": "Nuevo", "nif": "12"})
        self.assertIn(providers.BAD_NIF, html)
        self.assertEqual(1, self.count(Proveedor))
        pid = self.provider("Transportes Rápidos")
        url = f"{BASE}/{pid}/evaluaciones/nueva"
        html = self.rerendered(url, EVALUATION_FORM | {"evaluador_id": str(self.eva),
                                                       "fecha": "2026-10-19"})
        self.assertIn(providers.EVALUATED_IN_FUTURE, html)
        self.assertIn("Entregas puntuales", html)
        self.assertIn('<option selected value="5">5</option>', self.select(html, "documentacion"))
        html = self.rerendered(url, EVALUATION_FORM | {"evaluador_id": str(self.eva),
                                                       "calidad": ""})
        self.assertIn('aria-invalid="true"', self.select(html, "calidad"))
        self.assertEqual(0, self.count(EvaluacionProveedor))
        eid = self.evaluate(pid)
        html = self.rerendered(f"{BASE}/{pid}/evaluaciones/{eid}/editar", EVALUATION_FORM | {
            "evaluador_id": str(self.eva), "fecha_proxima": "2026-10-01",
            "comentario": "Revisado"})
        self.assertIn(providers.NEXT_NOT_AFTER, html)
        self.assertIn("Revisado", html)
        self.assertIsNone(self.load(EvaluacionProveedor, eid).comentario)


class CsrfTestCase(ProviderScreensBase):
    overrides = {"WTF_CSRF_ENABLED": True}

    def test_every_post_form_needs_its_token(self) -> None:
        pid = self.provider()
        eid = self.evaluate(pid)
        empty = self.provider("Sin evaluaciones")
        self.login(ADMIN)
        found = {}
        for page in (f"{BASE}/{pid}", f"{BASE}/{empty}"):
            forms = _PostForms()
            forms.feed(self.html(page))
            found |= {form["action"]: form for form in forms.forms}
        expected = {f"{BASE}/{record}/{decision}" for record in (pid, empty)
                    for decision in DECISIONS}
        expected |= {f"{BASE}/{pid}/evaluaciones/{eid}/eliminar", f"{BASE}/{empty}/eliminar"}
        self.assertEqual(expected, set(found))
        for action, form in found.items():
            with self.subTest(action=action):
                self.assertTrue(form["fields"].get("csrf_token"))
                self.assertEqual(400, self.client.post(action, data={"motivo": "x"}).status_code)
        for url in (f"{BASE}/nuevo", f"{BASE}/{pid}/editar", f"{BASE}/{pid}/evaluaciones/nueva",
                    f"{BASE}/{pid}/evaluaciones/{eid}/editar"):
            with self.subTest(url=url):
                self.assertIn('name="csrf_token"', self.html(url))
                self.assertEqual(400, self.client.post(url, data={"nombre": "x"}).status_code)
        self.assertEqual(EstadoProveedor.pendiente, self.load(Proveedor, pid).estado)
        action = f"{BASE}/{pid}/aprobar"
        self.post(action, found[action]["fields"], expect=f"{BASE}/{pid}")
        self.assertEqual(EstadoProveedor.aprobado, self.load(Proveedor, pid).estado)
