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

"""Marshmallow schemas of the JSON registers: each dumps its model's columns.

The auto schemas leave foreign keys and relations out, so the process and role
schemas declare them (RG-3 of ``process-role-registers``): a process dumps its
owner (``responsable_id``) and the ids of its related processes
(``relacionados``, both directions, as the MCP module shows them); a role dumps
the role it reports to (``reporta_a_id``). The routes never load with them: the
services validate every write.
"""

from marshmallow import fields
from sqlalchemy.orm import object_session

from .extensions import ma
from .models import (
    ParteInteresada,
    RolResponsabilidad,
    RecursoCapacitacion,
    ProcesoOperacion,
    AuditoriaIndicador,
    Mejora,
)
from .services import process_operations

# Esquema para Partes Interesadas
class ParteInteresadaSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = ParteInteresada
        load_instance = True

# Esquema para Roles y Responsabilidades
class RolResponsabilidadSchema(ma.SQLAlchemyAutoSchema):
    reporta_a_id = ma.auto_field()

    class Meta:
        model = RolResponsabilidad
        load_instance = True

# Esquema para Recursos y Capacitación
class RecursoCapacitacionSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = RecursoCapacitacion
        load_instance = True

# Esquema para Procesos de Operación
class ProcesoOperacionSchema(ma.SQLAlchemyAutoSchema):
    responsable_id = ma.auto_field()
    relacionados = fields.Method("_related_ids", dump_only=True)

    class Meta:
        model = ProcesoOperacion
        load_instance = True

    def _related_ids(self, process: ProcesoOperacion) -> list[int]:
        """Ids of the processes related to ``process``, ascending."""
        return process_operations.related_ids(object_session(process), process.id_proceso)

# Esquema para Auditorías e Indicadores
class AuditoriaIndicadorSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = AuditoriaIndicador
        load_instance = True

# Esquema para Mejoras
class MejoraSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = Mejora
        load_instance = True
