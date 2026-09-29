"""PDF exports use the print theme with embedded local fonts."""

from __future__ import annotations

import re
import unittest
import zlib
from datetime import date

import test_auth_bootstrap as bootstrap
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import (
    Auditoria,
    Capacitacion,
    EstadoAuditoriaEnum,
    NoConformidad,
    RoleEnum,
    SatisfaccionCliente,
    User,
)


PASSWORD = "StrongPassword123!"


def pdf_text(pdf: bytes) -> bytes:
    """Raw PDF bytes plus every Flate stream decompressed.

    WeasyPrint writes font dictionaries inside compressed object streams,
    so embedded font names are not visible in the raw bytes.
    """
    chunks = [pdf]
    for stream in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            chunks.append(zlib.decompress(stream))
        except zlib.error:
            pass
    return b"\n".join(chunks)
DAY = date(2026, 10, 5)
EXPORTS = (
    ("/auditorias/exportar_pdf/1", "auditoria_1.pdf"),
    ("/no_conformidades/exportar_pdf/1", "no_conformidad_1.pdf"),
    ("/capacitaciones/exportar_pdf/1", "capacitacion_1.pdf"),
    ("/satisfaccion_cliente/exportar_pdf/1", "encuesta_1.pdf"),
)


class PdfExportsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.app = bootstrap.build_app()
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            db.session.add_all([
                User(username="admin", email="admin@example.com",
                     password=generate_password_hash(PASSWORD), role=RoleEnum.ADMINISTRADOR),
                Auditoria(area_auditada="Compras", fecha=DAY, auditor="M. López",
                          resultado="Sin hallazgos", estado=EstadoAuditoriaEnum.EN_PROCESO),
                NoConformidad(descripcion="Etiqueta ilegible", fecha_detectada=DAY),
                Capacitacion(tema="ISO 9001", fecha=DAY, personal="Equipo", duracion_horas=4),
                SatisfaccionCliente(fecha_encuesta=DAY, cliente="Farmacia Sol", puntuacion=9),
            ])
            db.session.commit()
        self.client.post("/login", data={"username": "admin", "password": PASSWORD})

    def tearDown(self) -> None:
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_exports_are_themed_pdfs_with_embedded_local_fonts(self) -> None:
        for path, filename in EXPORTS:
            with self.subTest(path=path):
                with self.assertNoLogs("weasyprint", level="WARNING"):
                    response = self.client.get(path)
                self.assertEqual(200, response.status_code)
                self.assertEqual("application/pdf", response.mimetype)
                self.assertIn(filename, response.headers["Content-Disposition"])
                self.assertTrue(response.data.startswith(b"%PDF"))
                content = pdf_text(response.data)
                self.assertTrue(b"+Barlow-Condensed" in content, "heading font not embedded")
                self.assertTrue(b"+IBM-Plex-Sans" in content, "body font not embedded")

    def test_audit_pdf_includes_the_status(self) -> None:
        with self.app.test_request_context():
            from flask import render_template
            with self.app.app_context():
                auditoria = db.session.get(Auditoria, 1)
                html = render_template("auditorias/pdf_template.html", auditoria=auditoria,
                                       generado=DAY)
        self.assertIn("En Proceso", html)
        self.assertIn('class="doc-header"', html)

    def test_monthly_report_pdf_uses_the_print_theme(self) -> None:
        from app.utils import reports

        with self.app.app_context(), self.assertNoLogs("weasyprint", level="WARNING"):
            pdf = reports.generar_reporte_pdf(DAY)
        content = pdf_text(pdf)
        self.assertTrue(b"+Barlow-Condensed" in content, "heading font not embedded")
        self.assertTrue(b"+IBM-Plex-Sans" in content, "body font not embedded")


if __name__ == "__main__":
    unittest.main()
