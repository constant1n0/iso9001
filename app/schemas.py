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
