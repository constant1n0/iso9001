from .extensions import db
from flask_login import UserMixin
import enum
from datetime import datetime

# Enum para definir el tipo de evaluación: Riesgo u Oportunidad
class TipoEnum(enum.Enum):
    Riesgo = 'Riesgo'
    Oportunidad = 'Oportunidad'

# Modelo para almacenar Partes Interesadas
class ParteInteresada(db.Model):
    """
    Modelo para registrar partes interesadas y sus necesidades, expectativas
    y objetivos estratégicos en el SGC.
    """
    __tablename__ = 'partes_interesadas'
    id_interesado = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False, unique=True, index=True)
    necesidades_expectativas = db.Column(db.Text)
    requisitos_identificados = db.Column(db.Text)
    objetivo_estrategico = db.Column(db.Text)

# Modelo para Roles y Responsabilidades dentro del SGC
class RolResponsabilidad(db.Model):
    """
    Define los roles y responsabilidades del personal, incluyendo el compromiso
    con la calidad y descripción de la política de calidad.
    """
    __tablename__ = 'roles_responsabilidades'
    id_rol = db.Column(db.Integer, primary_key=True)
    rol = db.Column(db.String(50), nullable=False, unique=True, index=True)
    compromiso_calidad = db.Column(db.Boolean, default=False)
    descripcion_politica_calidad = db.Column(db.Text)

# Modelo para gestionar Riesgos y Oportunidades dentro del SGC
class RiesgoOportunidad(db.Model):
    """
    Modelo para registrar y gestionar los riesgos y oportunidades asociados al
    sistema de gestión de calidad.
    """
    __tablename__ = 'riesgos_oportunidades'
    id_riesgo = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.Enum(TipoEnum), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    objetivo_calidad = db.Column(db.Text)
    plan_accion = db.Column(db.Text)

# Modelo para Recursos y Capacitación del personal
class RecursoCapacitacion(db.Model):
    """
    Registra los recursos necesarios para la capacitación y el desarrollo del
    personal en relación con la calidad.
    """
    __tablename__ = 'recursos_capacitacion'
    id_recurso = db.Column(db.Integer, primary_key=True)
    recurso_necesario = db.Column(db.Text, nullable=False)
    capacitacion_personal = db.Column(db.Boolean, default=False)
    descripcion_documentacion = db.Column(db.Text)

# Modelo para la gestión de Procesos de Operación
class ProcesoOperacion(db.Model):
    """
    Almacena los procesos de operación, criterios de calidad, y aspectos de
    control de proveedores y no conformidades.
    """
    __tablename__ = 'procesos_operacion'
    id_proceso = db.Column(db.Integer, primary_key=True)
    proceso = db.Column(db.String(100), nullable=False, unique=True, index=True)
    criterio_calidad = db.Column(db.Text)
    control_proveedor = db.Column(db.Boolean, default=False)
    no_conformidad = db.Column(db.Text)

# Modelo para Auditorías e Indicadores
class AuditoriaIndicador(db.Model):
    """
    Registra las auditorías realizadas y los indicadores de desempeño
    relacionados con la calidad.
    """
    __tablename__ = 'auditorias_indicadores'
    id_auditoria = db.Column(db.Integer, primary_key=True)
    area_auditoria = db.Column(db.String(50), nullable=False)
    fecha_auditoria = db.Column(db.DateTime, default=datetime.utcnow)
    resultado = db.Column(db.Text)
    accion_correctiva = db.Column(db.Text)
    indicador_desempeno = db.Column(db.Text)

# Modelo para Mejoras Continuas dentro del SGC
class Mejora(db.Model):
    """
    Almacena registros de mejoras aplicadas, incluyendo no conformidades y
    acciones correctivas y preventivas.
    """
    __tablename__ = 'mejoras'
    id_mejora = db.Column(db.Integer, primary_key=True)
    no_conformidad = db.Column(db.Text, nullable=False)
    accion_correctiva = db.Column(db.Text)
    accion_preventiva = db.Column(db.Text)
    fecha_implementacion = db.Column(db.DateTime, default=datetime.utcnow)

# Modelo para Usuarios (para autenticación y gestión de accesos)
class User(UserMixin, db.Model):
    """
    Modelo de usuario que permite gestionar la autenticación y autorización en
    el sistema. Implementa UserMixin para integrarse con Flask-Login.
    """
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False, unique=True, index=True)
    password = db.Column(db.String(200), nullable=False)

    def __repr__(self):
        return f'<User {self.username}>'

# Nuevos Modelos Propuestos para la Gestión de Calidad (Informes)

# Modelo para registrar No Conformidades dentro del SGC
class NoConformidad(db.Model):
    """
    Almacena registros de no conformidades detectadas, sus responsables y
    acciones correctivas.
    """
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
    """
    Guarda los resultados de encuestas de satisfacción del cliente, incluyendo
    puntuaciones y comentarios.
    """
    __tablename__ = 'satisfaccion_cliente'
    id = db.Column(db.Integer, primary_key=True)
    fecha_encuesta = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    cliente = db.Column(db.String(100), nullable=False)
    puntuacion = db.Column(db.Integer, nullable=False)  # Supongamos de 1 a 10
    comentarios = db.Column(db.Text)

# Modelo para registrar Capacitaciones del Personal
class Capacitacion(db.Model):
    """
    Registra capacitaciones impartidas al personal, incluyendo el tema, fecha,
    duración, y evaluación final.
    """
    __tablename__ = 'capacitaciones'
    id = db.Column(db.Integer, primary_key=True)
    tema = db.Column(db.String(100), nullable=False)
    fecha = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    personal = db.Column(db.String(100), nullable=False)
    duracion_horas = db.Column(db.Integer)
    evaluacion_final = db.Column(db.String(20))
