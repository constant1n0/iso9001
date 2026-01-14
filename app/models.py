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

from .extensions import db
from flask_login import UserMixin
import enum
from datetime import datetime

# Enum para definir el tipo de evaluación: Riesgo u Oportunidad
class TipoEnum(enum.Enum):
    Riesgo = 'Riesgo'
    Oportunidad = 'Oportunidad'

# Enum para definir los roles de usuario
class RoleEnum(enum.Enum):
    ADMINISTRADOR = 'Administrador'
    AUDITOR = 'Auditor'
    OPERATIVO = 'Operativo'

# Modelo para almacenar Partes Interesadas
class ParteInteresada(db.Model):
    __tablename__ = 'partes_interesadas'
    id_interesado = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False, unique=True, index=True)
    necesidades_expectativas = db.Column(db.Text)
    requisitos_identificados = db.Column(db.Text)
    objetivo_estrategico = db.Column(db.Text)

# Modelo para Roles y Responsabilidades dentro del SGC
class RolResponsabilidad(db.Model):
    __tablename__ = 'roles_responsabilidades'
    id_rol = db.Column(db.Integer, primary_key=True)
    rol = db.Column(db.String(50), nullable=False, unique=True, index=True)
    compromiso_calidad = db.Column(db.Boolean, default=False)
    descripcion_politica_calidad = db.Column(db.Text)

# Modelo para gestionar Riesgos y Oportunidades dentro del SGC
class RiesgoOportunidad(db.Model):
    __tablename__ = 'riesgos_oportunidades'
    id_riesgo = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.Enum(TipoEnum), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    objetivo_calidad = db.Column(db.Text)
    plan_accion = db.Column(db.Text)

# Modelo para Recursos y Capacitación del personal
class RecursoCapacitacion(db.Model):
    __tablename__ = 'recursos_capacitacion'
    id_recurso = db.Column(db.Integer, primary_key=True)
    recurso_necesario = db.Column(db.Text, nullable=False)
    capacitacion_personal = db.Column(db.Boolean, default=False)
    descripcion_documentacion = db.Column(db.Text)

# Modelo para la gestión de Procesos de Operación
class ProcesoOperacion(db.Model):
    __tablename__ = 'procesos_operacion'
    id_proceso = db.Column(db.Integer, primary_key=True)
    proceso = db.Column(db.String(100), nullable=False, unique=True, index=True)
    criterio_calidad = db.Column(db.Text)
    control_proveedor = db.Column(db.Boolean, default=False)
    no_conformidad = db.Column(db.Text)

# Modelo para Auditorías e Indicadores
class AuditoriaIndicador(db.Model):
    __tablename__ = 'auditorias_indicadores'
    id_auditoria = db.Column(db.Integer, primary_key=True)
    area_auditoria = db.Column(db.String(50), nullable=False)
    fecha_auditoria = db.Column(db.DateTime, default=datetime.utcnow)
    resultado = db.Column(db.Text)
    accion_correctiva = db.Column(db.Text)
    indicador_desempeno = db.Column(db.Text)

# Modelo para Mejoras Continuas dentro del SGC
class Mejora(db.Model):
    __tablename__ = 'mejoras'
    id_mejora = db.Column(db.Integer, primary_key=True)
    no_conformidad = db.Column(db.Text, nullable=False)
    accion_correctiva = db.Column(db.Text)
    accion_preventiva = db.Column(db.Text)
    fecha_implementacion = db.Column(db.DateTime, default=datetime.utcnow)

# Modelo para Usuarios (para autenticación y gestión de accesos)
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False, unique=True, index=True)
    email = db.Column(db.String(255), nullable=True, unique=True, index=True)
    password = db.Column(db.String(256), nullable=False)
    role = db.Column(db.Enum(RoleEnum), nullable=False, default=RoleEnum.OPERATIVO)

    def __repr__(self):
        return f'<User {self.username}>'

# Modelo para registrar No Conformidades dentro del SGC
class NoConformidad(db.Model):
    __tablename__ = 'no_conformidades'
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.Text, nullable=False)
    fecha_detectada = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    responsable = db.Column(db.String(50))
    estado = db.Column(db.String(20), nullable=False, default="Abierta")
    accion_correctiva = db.Column(db.Text)
    fecha_cierre = db.Column(db.Date)

# Modelo para almacenar resultados de Satisfacción del Cliente
class SatisfaccionCliente(db.Model):
    __tablename__ = 'satisfaccion_cliente'
    id = db.Column(db.Integer, primary_key=True)
    fecha_encuesta = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    cliente = db.Column(db.String(100), nullable=False)
    puntuacion = db.Column(db.Integer, nullable=False)
    comentarios = db.Column(db.Text)

# Modelo para registrar Capacitaciones del Personal
class Capacitacion(db.Model):
    __tablename__ = 'capacitaciones'
    id = db.Column(db.Integer, primary_key=True)
    tema = db.Column(db.String(100), nullable=False)
    fecha = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    personal = db.Column(db.String(100), nullable=False)
    duracion_horas = db.Column(db.Integer)
    evaluacion_final = db.Column(db.String(20))

# Enum para definir el estado de las auditorías
class EstadoAuditoriaEnum(enum.Enum):
    PENDIENTE = 'Pendiente'
    EN_PROCESO = 'En Proceso'
    COMPLETADA = 'Completada'
    CANCELADA = 'Cancelada'

# Modelo para Auditorías
class Auditoria(db.Model):
    __tablename__ = 'auditorias'
    id = db.Column(db.Integer, primary_key=True)
    area_auditada = db.Column(db.String(50), nullable=False)
    fecha = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    auditor = db.Column(db.String(50), nullable=False)
    resultado = db.Column(db.Text, nullable=False)
    accion_correctiva = db.Column(db.Text)
    estado = db.Column(db.Enum(EstadoAuditoriaEnum), nullable=False, default=EstadoAuditoriaEnum.PENDIENTE)

    def __repr__(self):
        return f'<Auditoria {self.area_auditada}>'

# Modelo para Control Documental
class DocumentCategory(enum.Enum):
    MANUAL_CALIDAD = 'Manual de Calidad'
    PROCEDIMIENTO_OPERATIVO = 'Procedimiento Operativo'
    INSTRUCCION_TRABAJO = 'Instrucción de Trabajo'
    PLAN_ACCION_CORRECTIVA = 'Plan de Acción Correctiva'
    PLAN_ACCION_PREVENTIVA = 'Plan de Acción Preventiva'
    REGISTRO_CALIDAD = 'Registro de Calidad'
    INFORME_REVISION = 'Informe de Revisión por la Dirección'
    POLITICA_SEGURIDAD = 'Política de Seguridad y Salud Ocupacional'
    INDICADOR_DESEMPENO = 'Indicador de Desempeño'
    PLAN_CAPACITACION = 'Plan de Capacitación'
    OTRO = 'Otro'

class Document(db.Model):
    __tablename__ = 'documents'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(50), nullable=False, unique=True, index=True)
    category = db.Column(db.Enum(DocumentCategory), nullable=False)
    version = db.Column(db.String(10), nullable=False, default="1.0")
    issued_date = db.Column(db.Date, default=datetime.utcnow)
    approved_by = db.Column(db.String(100), nullable=True)
    signature = db.Column(db.String(255), nullable=True)
    content = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f'<Document {self.title} - {self.version}>'
