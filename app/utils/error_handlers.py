# Este archivo es parte de "ABSOLUT ISO9001".
#
# "ABSOLUT ISO9001" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ABSOLUT ISO9001" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

from flask import jsonify, current_app
import logging

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


def register_error_handlers(app):
    """Registra todos los manejadores de errores en la aplicación."""
    app.register_error_handler(Exception, handle_exception)
    app.register_error_handler(404, handle_404)
    app.register_error_handler(403, handle_403)
    app.register_error_handler(400, handle_400)
