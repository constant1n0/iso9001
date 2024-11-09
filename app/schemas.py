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

from .extensions import ma
from .models import (
    ParteInteresada,
    RolResponsabilidad,
    RiesgoOportunidad,
    RecursoCapacitacion,
    ProcesoOperacion,
    AuditoriaIndicador,
    Mejora,
    TipoEnum
)
from marshmallow import fields

# Esquema para Partes Interesadas
class ParteInteresadaSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = ParteInteresada
        load_instance = True

# Esquema para Roles y Responsabilidades
class RolResponsabilidadSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = RolResponsabilidad
        load_instance = True

# Esquema para Riesgos y Oportunidades
class RiesgoOportunidadSchema(ma.SQLAlchemyAutoSchema):
    tipo = fields.Enum(TipoEnum, by_value=True)

    class Meta:
        model = RiesgoOportunidad
        load_instance = True

# Esquema para Recursos y Capacitación
class RecursoCapacitacionSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = RecursoCapacitacion
        load_instance = True

# Esquema para Procesos de Operación
class ProcesoOperacionSchema(ma.SQLAlchemyAutoSchema):
    class Meta:
        model = ProcesoOperacion
        load_instance = True

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
