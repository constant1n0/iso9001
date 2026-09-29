# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""Render templates to PDF with the print theme (app/static/css/pdf.css)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Response, current_app, make_response, render_template
from weasyprint import HTML


def render_pdf(template: str, **context) -> bytes:
    """Render a template extending _partials/pdf_base.html to PDF bytes.

    Relative URLs (stylesheet, fonts) resolve against the static folder.
    """
    context.setdefault(
        "generado", datetime.now(ZoneInfo(current_app.config["APP_TIMEZONE"])).date()
    )
    html = render_template(template, **context)
    return HTML(string=html, base_url=f"{current_app.static_folder}/").write_pdf()


def pdf_response(pdf: bytes, filename: str) -> Response:
    """Serve PDF bytes inline in the browser."""
    response = make_response(pdf)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"inline; filename={filename}"
    return response
