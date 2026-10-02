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

from flask import current_app, flash, jsonify, redirect, request, url_for
import logging

from ..services.errors import (
    Conflict,
    DomainError,
    NotFound,
    PermissionDenied,
    ValidationError,
)

# Configurar logging para errores
logger = logging.getLogger(__name__)

def handle_exception(e):
    """
    Manejador global de excepciones que no expone información sensible.
    Los detalles del error se registran en el log pero no se envían al cliente.
    """
    # Registrar el error completo en los logs para debugging
    logger.error(f"Error no manejado: {str(e)}", exc_info=True)

    # En modo desarrollo, mostrar más detalles (solo si DEBUG está activo)
    if current_app.config.get('DEBUG', False):
        return jsonify({
            'message': 'Error interno del servidor',
            'error': str(e),
            'type': type(e).__name__
        }), 500

    # En producción, no exponer detalles del error
    return jsonify({
        'message': 'Ha ocurrido un error interno. Por favor, contacte al administrador.'
    }), 500


def handle_404(e):
    """Manejador para errores 404 (recurso no encontrado)."""
    return jsonify({'message': 'Recurso no encontrado'}), 404


def handle_403(e):
    """Manejador para errores 403 (acceso denegado)."""
    return jsonify({'message': 'Acceso denegado'}), 403


def handle_400(e):
    """Manejador para errores 400 (solicitud inválida)."""
    return jsonify({'message': 'Solicitud inválida'}), 400


_DOMAIN_STATUS = {
    NotFound: 404,
    Conflict: 409,
    PermissionDenied: 403,
    ValidationError: 422,
}


def _wants_json() -> bool:
    """True for JSON bodies or clients that prefer JSON over HTML."""
    return request.is_json or (
        request.accept_mimetypes.best_match(['text/html', 'application/json'])
        == 'application/json'
    )


def handle_domain_error(e: DomainError):
    """Traduce un error de dominio en una respuesta HTTP, nunca en un 500.

    JSON: ``{"error": mensaje}`` con 404/409/403/422. HTML: un
    ``PermissionDenied`` replica a ``role_required`` (aviso y redirección al
    panel); el resto responde igual que los demás manejadores (JSON).
    """
    status = next(
        (code for cls, code in _DOMAIN_STATUS.items() if isinstance(e, cls)), 400
    )
    if isinstance(e, PermissionDenied) and not _wants_json():
        flash('No tienes permiso para acceder a esta página.', 'danger')
        return redirect(url_for('dashboard.dashboard'))
    return jsonify({'error': e.message}), status


def register_error_handlers(app):
    """Registra todos los manejadores de errores en la aplicación."""
    # Flask resolves handlers by exception MRO, so DomainError always wins
    # over the generic Exception handler regardless of registration order.
    app.register_error_handler(DomainError, handle_domain_error)
    app.register_error_handler(Exception, handle_exception)
    app.register_error_handler(404, handle_404)
    app.register_error_handler(403, handle_403)
    app.register_error_handler(400, handle_400)
