"""Independent CRLF probes: request focus 1, 4 and 5; MCP parity."""
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import test_auth_bootstrap as bootstrap
from register_routes import RegisterRoutesBase
from mcp_support import McpDbCase, mcp_actor
from test_communication_independent import BrowserForm, hashes
from app.extensions import db
from app.mcp_server.registry import MODULES
from app.models import AuditLog, RoleEnum, ParteInteresada, ObjetivoCalidad, KnowledgeItem
from app.services import people

TEXT = "  Primera <línea>\nSegunda & última  "
CASES = (
    ("partes_interesadas", "necesidades_expectativas", "/partes_interesadas/editar/1"),
    ("objetivos_calidad", "descripcion", "/objetivos-calidad/1/editar"),
    ("knowledge", "summary", "/conocimientos/1/editar"),
)


class IndependentBrowser(RegisterRoutesBase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.file = Path(self.storage.name) / "probe.sqlite"
        build = bootstrap.build_app
        patcher = patch.object(bootstrap, "build_app", lambda: build(
            SQLALCHEMY_DATABASE_URI="sqlite:///" + str(self.file)))
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()
        self.login(RoleEnum.ADMINISTRADOR)
        self.seed_with(people, {"nombre": "Owner"})
        from mcp_support import SEEDS
        from datetime import date
        for slug, key, _ in CASES:
            values = SEEDS[slug] | {key: TEXT}
            for name in ("fecha_inicio", "fecha_limite"):
                if name in values:
                    values[name] = date.fromisoformat(values[name])
            self.seed_with(MODULES[slug].service, values)

    def form(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_three_real_forms_crlf_noops_and_rejections(self):
        for _, key, path in CASES:
            form = self.form(path)
            self.assertEqual(TEXT, form[key])
            form[key] = TEXT.replace("\n", "\r\n")
            before = hashlib.sha256(self.file.read_bytes()).hexdigest()
            self.assertEqual(302, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashlib.sha256(self.file.read_bytes()).hexdigest())
            form["nombre" if key == "necesidades_expectativas" else
                 "titulo" if key == "descripcion" else "title"] = ""
            self.assertEqual(200, self.client.post(path, data=form).status_code)
            self.assertEqual(before, hashlib.sha256(self.file.read_bytes()).hexdigest())

    def test_legacy_row_normalises_once_with_or_without_real_edit(self):
        for real_edit in (False, True):
            for slug, key, path in CASES:
                model = dict(partes_interesadas=ParteInteresada, objetivos_calidad=ObjetivoCalidad, knowledge=KnowledgeItem)[slug]
                with self.app.app_context():
                    db.session.execute(model.__table__.update().values(
                        **{key: TEXT.replace("\n", "\r\n")}))
                    db.session.commit()
                    count = AuditLog.query.count()
                form = self.form(path)
                form[key] = (TEXT + (" edit" if real_edit else "")).replace("\n", "\r\n")
                self.assertEqual(302, self.client.post(path, data=form).status_code)
                with self.app.app_context():
                    self.assertEqual(count + 1, AuditLog.query.count())
                    self.assertEqual(TEXT + (" edit" if real_edit else ""), db.session.scalar(db.select(getattr(model, key))))
                before = self.file.read_bytes()
                self.assertEqual(302, self.client.post(path, data=self.form(path)).status_code)
                self.assertEqual(before, self.file.read_bytes())


class IndependentMcp(McpDbCase):
    async def test_crlf_noops_preserve_whitespace_and_database(self):
        for slug, key, _ in CASES:
            id = self.seed(slug, **{key: TEXT})
            before = hashes()
            result = await self.call(mcp_actor(), "qms_update", {
                "module": slug, "id": id, "data": {key: TEXT.replace("\n", "\r\n")}})
            self.assertFalse(result.is_error, result.content)
            self.assertEqual(TEXT, result.structured_content[key])
            self.assertEqual(before, hashes())
