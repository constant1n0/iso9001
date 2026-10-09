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

from __future__ import annotations

import enum
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime

from flask import current_app
from flask_login import UserMixin
from itsdangerous import BadData, URLSafeTimedSerializer as Serializer
from sqlalchemy import BigInteger, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declared_attr

from .extensions import db


PASSWORD_RESET_SALT = 'password-reset-salt'
PASSWORD_STATE_DOMAIN = b'iso9001-password-reset-state-v1\x00'


@dataclass(frozen=True)
class ResetTokenVerification:
    """Verified reset subject and the password state used by its token."""

    user: User
    expected_password_hash: str

# Enum para definir el tipo de evaluación: Riesgo u Oportunidad
class TipoEnum(enum.Enum):
    Riesgo = 'Riesgo'
    Oportunidad = 'Oportunidad'

# Enum para definir los roles de usuario
class RoleEnum(enum.Enum):
    ADMINISTRADOR = 'Administrador'
    AUDITOR = 'Auditor'
    OPERATIVO = 'Operativo'

class RecordMetadataMixin:
    """Who created and last changed a record, and when.

    Columns are nullable with no default of any kind: services stamp them
    explicitly (see ``services.attribution``) and rows that predate the
    columns keep NULL instead of an invented attribution.
    """

    created_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=True)

    @declared_attr
    def created_by_id(cls):
        return db.Column(
            db.Integer,
            db.ForeignKey(
                'users.id',
                name=f'fk_{cls.__tablename__}_created_by_id_users',
                ondelete='SET NULL',
            ),
            nullable=True,
        )

    @declared_attr
    def updated_by_id(cls):
        return db.Column(
            db.Integer,
            db.ForeignKey(
                'users.id',
                name=f'fk_{cls.__tablename__}_updated_by_id_users',
                ondelete='SET NULL',
            ),
            nullable=True,
        )


# Modelo para almacenar Partes Interesadas
class ParteInteresada(RecordMetadataMixin, db.Model):
    __tablename__ = 'partes_interesadas'
    id_interesado = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False, unique=True, index=True)
    necesidades_expectativas = db.Column(db.Text)
    requisitos_identificados = db.Column(db.Text)
    objetivo_estrategico = db.Column(db.Text)

# Modelo para Roles y Responsabilidades dentro del SGC
class RolResponsabilidad(RecordMetadataMixin, db.Model):
    __tablename__ = 'roles_responsabilidades'
    id_rol = db.Column(db.Integer, primary_key=True)
    rol = db.Column(db.String(50), nullable=False, unique=True, index=True)
    compromiso_calidad = db.Column(db.Boolean, default=False)
    descripcion_politica_calidad = db.Column(db.Text)

# Modelo para gestionar Riesgos y Oportunidades dentro del SGC
class RiesgoOportunidad(RecordMetadataMixin, db.Model):
    __tablename__ = 'riesgos_oportunidades'
    id_riesgo = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.Enum(TipoEnum), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    objetivo_calidad = db.Column(db.Text)
    plan_accion = db.Column(db.Text)

# Modelo para Recursos y Capacitación del personal
class RecursoCapacitacion(RecordMetadataMixin, db.Model):
    __tablename__ = 'recursos_capacitacion'
    id_recurso = db.Column(db.Integer, primary_key=True)
    recurso_necesario = db.Column(db.Text, nullable=False)
    capacitacion_personal = db.Column(db.Boolean, default=False)
    descripcion_documentacion = db.Column(db.Text)

# Modelo para la gestión de Procesos de Operación
class ProcesoOperacion(RecordMetadataMixin, db.Model):
    __tablename__ = 'procesos_operacion'
    id_proceso = db.Column(db.Integer, primary_key=True)
    proceso = db.Column(db.String(100), nullable=False, unique=True, index=True)
    criterio_calidad = db.Column(db.Text)
    control_proveedor = db.Column(db.Boolean, default=False)
    no_conformidad = db.Column(db.Text)

# Modelo para Auditorías e Indicadores
class AuditoriaIndicador(RecordMetadataMixin, db.Model):
    __tablename__ = 'auditorias_indicadores'
    id_auditoria = db.Column(db.Integer, primary_key=True)
    area_auditoria = db.Column(db.String(50), nullable=False)
    fecha_auditoria = db.Column(db.DateTime, default=datetime.utcnow)
    resultado = db.Column(db.Text)
    accion_correctiva = db.Column(db.Text)
    indicador_desempeno = db.Column(db.Text)

# Modelo para Mejoras Continuas dentro del SGC
class Mejora(RecordMetadataMixin, db.Model):
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
    # Users are deactivated, never deleted, so their records stay attributable.
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )

    @property
    def is_active(self) -> bool:
        """Flask-Login hook: an inactive user cannot log in or keep a session.

        Fails closed: an unsaved user whose column default has not been
        applied yet (``None``) is not active.
        """
        return self.active is True

    @staticmethod
    def _password_state_fingerprint(password_hash: str) -> str:
        """Return a secret-keyed fingerprint for the current password state."""
        secret_key = current_app.config['SECRET_KEY']
        key = (
            secret_key.encode('utf-8')
            if isinstance(secret_key, str)
            else bytes(secret_key)
        )
        message = PASSWORD_STATE_DOMAIN + password_hash.encode('utf-8')
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def get_reset_token(self) -> str:
        """Generate a timed token bound to the current password state."""
        serializer = Serializer(current_app.config['SECRET_KEY'])
        return serializer.dumps(
            {
                'user_id': self.id,
                'password_fingerprint': self._password_state_fingerprint(
                    self.password
                ),
            },
            salt=PASSWORD_RESET_SALT,
        )

    @staticmethod
    def verify_reset_token(
        token: str,
        expires_sec: int = 3600,
    ) -> ResetTokenVerification | None:
        """Verify a timed reset token against the user's password state."""
        serializer = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = serializer.loads(
                token,
                salt=PASSWORD_RESET_SALT,
                max_age=expires_sec,
            )
        except (BadData, TypeError, ValueError):
            return None

        if not isinstance(data, dict) or set(data) != {
            'user_id',
            'password_fingerprint',
        }:
            return None

        user_id = data['user_id']
        fingerprint = data['password_fingerprint']
        if (
            type(user_id) is not int
            or user_id <= 0
            or not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or not fingerprint.isascii()
            or any(
                character not in '0123456789abcdef'
                for character in fingerprint
            )
        ):
            return None

        try:
            user = db.session.get(User, user_id)
        except SQLAlchemyError:
            try:
                db.session.rollback()
            except SQLAlchemyError:
                pass
            return None
        if user is None or not user.is_active:
            return None

        expected_fingerprint = User._password_state_fingerprint(user.password)
        if not hmac.compare_digest(fingerprint, expected_fingerprint):
            return None

        return ResetTokenVerification(
            user=user,
            expected_password_hash=user.password,
        )

    @staticmethod
    def update_password_from_reset(
        user_id: int,
        expected_password_hash: str,
        new_password_hash: str,
    ) -> bool:
        """Atomically replace a password only if its verified state is current.

        The user must still be active, so a deactivation that lands between
        verification and update still wins.
        """
        try:
            result = db.session.execute(
                db.update(User)
                .where(
                    User.id == user_id,
                    User.password == expected_password_hash,
                    User.active.is_(True),
                )
                .values(password=new_password_hash)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                db.session.commit()
                return True
        except SQLAlchemyError:
            pass

        try:
            db.session.rollback()
        except SQLAlchemyError:
            pass
        return False

    def __repr__(self):
        return f'<User {self.username}>'

# Roles (``RolResponsabilidad``) held by each person; a plain table, not audited
# itself: role changes are recorded on the person's audit row.
persona_roles = db.Table(
    'persona_roles',
    db.Column('persona_id', db.Integer, nullable=False),
    db.Column('rol_id', db.Integer, nullable=False),
    db.PrimaryKeyConstraint('persona_id', 'rol_id', name='pk_persona_roles'),
    db.ForeignKeyConstraint(
        ['persona_id'],
        ['personas.id'],
        name='fk_persona_roles_persona_id_personas',
        ondelete='CASCADE',
    ),
    db.ForeignKeyConstraint(
        ['rol_id'],
        ['roles_responsabilidades.id_rol'],
        name='fk_persona_roles_rol_id_roles_responsabilidades',
        ondelete='CASCADE',
    ),
    db.Index('ix_persona_roles_rol_id', 'rol_id'),
)


class Person(RecordMetadataMixin, db.Model):
    """Someone who does work under the QMS (ISO 9001 clauses 5.3 and 7.2).

    Separate from login accounts: a person may be linked to at most one user
    (``user_id``, unique) and holds any number of QMS roles. People are
    deactivated (``activo``) rather than deleted once other records cite them.
    """

    __tablename__ = 'personas'
    __table_args__ = (
        db.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name='fk_personas_user_id_users',
            ondelete='SET NULL',
        ),
        db.UniqueConstraint('user_id', name='uq_personas_user_id'),
        db.UniqueConstraint('email', name='uq_personas_email'),
        db.Index('ix_personas_nombre', 'nombre'),
    )

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(255), nullable=True)  # stored lower-cased
    user_id = db.Column(db.Integer, nullable=True)
    activo = db.Column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    notas = db.Column(db.Text)

    # One-way on purpose: a back-reference would mark roles as changed (and
    # unaudited) whenever a person's roles change.
    roles = db.relationship(
        'RolResponsabilidad',
        secondary=persona_roles,
        order_by='RolResponsabilidad.rol',
        lazy='selectin',
    )

    @property
    def rol_ids(self) -> list[int]:
        """Ids of the roles held, ascending."""
        return sorted(role.id_rol for role in self.roles)

    def __repr__(self):
        return f'<Person {self.id} {self.nombre}>'


def person_link(table: str, column: str):
    """A nullable reference to ``personas`` that keeps a cited person from being deleted.

    Used where a record also keeps its legacy free-text name (decision Q4 of
    ``qms-people``); people are deactivated instead of deleted once cited.
    """
    return db.Column(
        db.Integer,
        db.ForeignKey(
            'personas.id',
            name=f'fk_{table}_{column}_personas',
            ondelete='RESTRICT',
        ),
        nullable=True,
        index=True,
    )


class CompetenceType(enum.Enum):
    """What a role requires (ISO 9001 clause 7.2): stored by name, shown by value."""

    educacion = 'Educación'
    formacion = 'Formación'
    habilidad = 'Habilidad'
    experiencia = 'Experiencia'


class CompetenceEvaluation(enum.Enum):
    """Effectiveness of the action taken to acquire a competence (decision Q5)."""

    pendiente = 'Pendiente'
    eficaz = 'Eficaz'
    no_eficaz = 'No eficaz'


def text_enum(enum_cls: type[enum.Enum], constraint: str) -> db.Enum:
    """An enum stored as its member names in a text column, checked by ``constraint``."""
    return db.Enum(
        enum_cls, native_enum=False, length=20, create_constraint=True, name=constraint
    )


class CompetenceRequirement(RecordMetadataMixin, db.Model):
    """Competence a role (``RolResponsabilidad``) requires (ISO 9001 clause 7.2)."""

    __tablename__ = 'competencias_requeridas'
    __table_args__ = (
        db.ForeignKeyConstraint(
            ['rol_id'],
            ['roles_responsabilidades.id_rol'],
            name='fk_competencias_requeridas_rol_id_roles_responsabilidades',
            ondelete='RESTRICT',
        ),
        db.Index('ix_competencias_requeridas_rol_id', 'rol_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    rol_id = db.Column(db.Integer, nullable=False)
    tipo = db.Column(
        text_enum(CompetenceType, 'ck_competencias_requeridas_tipo'), nullable=False
    )
    descripcion = db.Column(db.String(500), nullable=False)
    criterio = db.Column(db.Text)  # how the competence is evidenced


class CompetenceRecord(RecordMetadataMixin, db.Model):
    """Competence a person has demonstrated, with its evidence (ISO 9001 clause 7.2).

    It may meet a requirement and cite a training as evidence (decision Q5);
    deleting the training only clears the link. ``evaluacion_eficacia`` other
    than ``pendiente`` needs ``fecha_evaluacion`` and ``evaluador_id`` (a rule
    of ``services.competence``).
    """

    __tablename__ = 'competencias_acreditadas'
    __table_args__ = (
        db.ForeignKeyConstraint(
            ['persona_id'],
            ['personas.id'],
            name='fk_competencias_acreditadas_persona_id_personas',
            ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['requisito_id'],
            ['competencias_requeridas.id'],
            name='fk_competencias_acreditadas_requisito_id',  # the long form exceeds 63
            ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['capacitacion_id'],
            ['capacitaciones.id'],
            name='fk_competencias_acreditadas_capacitacion_id_capacitaciones',
            ondelete='SET NULL',
        ),
        db.ForeignKeyConstraint(
            ['evaluador_id'],
            ['personas.id'],
            name='fk_competencias_acreditadas_evaluador_id_personas',
            ondelete='RESTRICT',
        ),
        db.Index('ix_competencias_acreditadas_persona_id', 'persona_id'),
        db.Index('ix_competencias_acreditadas_requisito_id', 'requisito_id'),
        db.Index('ix_competencias_acreditadas_capacitacion_id', 'capacitacion_id'),
        db.Index('ix_competencias_acreditadas_evaluador_id', 'evaluador_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    persona_id = db.Column(db.Integer, nullable=False)
    requisito_id = db.Column(db.Integer, nullable=True)
    evidencia = db.Column(db.String(500), nullable=False)  # text or a reference
    capacitacion_id = db.Column(db.Integer, nullable=True)
    fecha_obtencion = db.Column(db.Date, nullable=False)
    fecha_caducidad = db.Column(db.Date)
    evaluacion_eficacia = db.Column(
        text_enum(CompetenceEvaluation, 'ck_competencias_acreditadas_evaluacion_eficacia'),
        nullable=False,
        default=CompetenceEvaluation.pendiente,
        server_default=CompetenceEvaluation.pendiente.name,
    )
    fecha_evaluacion = db.Column(db.Date)
    evaluador_id = db.Column(db.Integer, nullable=True)


class EstadoNoConformidad(enum.Enum):
    """Where a nonconformity stands (ISO 9001 clause 10.2, decision N2 of ``nc-capa-loop``).

    Only the service changes it (``nonconformities``); ``cerrada`` and
    ``cancelada`` are terminal until an administrator reopens the record.
    """

    abierta = 'Abierta'
    accion_planificada = 'Acción planificada'
    en_verificacion = 'En verificación'
    cerrada = 'Cerrada'
    cancelada = 'Cancelada'


class OrigenNoConformidad(enum.Enum):
    """Where a nonconformity was detected (decision N1)."""

    auditoria = 'Auditoría'
    cliente = 'Cliente'
    proceso = 'Proceso'
    proveedor = 'Proveedor'
    otro = 'Otro'


class GravedadNoConformidad(enum.Enum):
    """How serious a nonconformity is (decision N1)."""

    mayor = 'Mayor'
    menor = 'Menor'
    observacion = 'Observación'


# Modelo para registrar No Conformidades dentro del SGC
class NoConformidad(RecordMetadataMixin, db.Model):
    __tablename__ = 'no_conformidades'
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.Text, nullable=False)
    fecha_detectada = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    responsable = db.Column(db.String(50))  # legacy free text, kept as written
    responsable_id = person_link('no_conformidades', 'responsable_id')
    estado = db.Column(
        text_enum(EstadoNoConformidad, 'ck_no_conformidades_estado'),
        nullable=False,
        default=EstadoNoConformidad.abierta,
        server_default=EstadoNoConformidad.abierta.name,
    )
    origen = db.Column(text_enum(OrigenNoConformidad, 'ck_no_conformidades_origen'))
    gravedad = db.Column(text_enum(GravedadNoConformidad, 'ck_no_conformidades_gravedad'))
    contencion = db.Column(db.Text)  # immediate containment of the effects
    causa_raiz = db.Column(db.Text)
    accion_correctiva = db.Column(db.Text)  # legacy free text (decision N6)
    fecha_cierre = db.Column(db.Date)  # set on closing or cancelling, cleared on reopening
    motivo_cancelacion = db.Column(db.Text)

# Modelo para almacenar resultados de Satisfacción del Cliente
class SatisfaccionCliente(RecordMetadataMixin, db.Model):
    __tablename__ = 'satisfaccion_cliente'
    id = db.Column(db.Integer, primary_key=True)
    fecha_encuesta = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    cliente = db.Column(db.String(100), nullable=False)
    puntuacion = db.Column(db.Integer, nullable=False)
    comentarios = db.Column(db.Text)

# Modelo para registrar Capacitaciones del Personal
class Capacitacion(RecordMetadataMixin, db.Model):
    __tablename__ = 'capacitaciones'
    id = db.Column(db.Integer, primary_key=True)
    tema = db.Column(db.String(100), nullable=False)
    fecha = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    personal = db.Column(db.String(100), nullable=False)  # legacy free text
    persona_id = person_link('capacitaciones', 'persona_id')
    duracion_horas = db.Column(db.Integer)
    evaluacion_final = db.Column(db.String(20))

# Enum para definir el estado de las auditorías
class EstadoAuditoriaEnum(enum.Enum):
    PENDIENTE = 'Pendiente'
    EN_PROCESO = 'En Proceso'
    COMPLETADA = 'Completada'
    CANCELADA = 'Cancelada'

# Modelo para Auditorías
class Auditoria(RecordMetadataMixin, db.Model):
    __tablename__ = 'auditorias'
    id = db.Column(db.Integer, primary_key=True)
    area_auditada = db.Column(db.String(50), nullable=False)
    fecha = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    auditor = db.Column(db.String(50), nullable=False)  # legacy free text
    auditor_id = person_link('auditorias', 'auditor_id')
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

class Document(RecordMetadataMixin, db.Model):
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


# Registro de auditoría append-only: quién cambió qué y cuándo (ISO 9001 7.5.3)
class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    __table_args__ = (
        db.PrimaryKeyConstraint('id', name='pk_audit_logs'),
        db.ForeignKeyConstraint(
            ['actor_user_id'],
            ['users.id'],
            name='fk_audit_logs_actor_user_id_users',
            ondelete='SET NULL',
        ),
        db.CheckConstraint(
            "action IN ('create', 'update', 'delete')",
            name='ck_audit_logs_action',
        ),
        db.CheckConstraint(
            "channel IN ('web', 'mcp', 'cli', 'system')",
            name='ck_audit_logs_channel',
        ),
        db.Index('ix_audit_logs_entity', 'entity_type', 'entity_id', 'id'),
        db.Index('ix_audit_logs_occurred_at', 'occurred_at'),
        db.Index('ix_audit_logs_actor_user_id', 'actor_user_id'),
    )

    id = db.Column(BigInteger().with_variant(Integer, 'sqlite'), autoincrement=True)
    occurred_at = db.Column(
        db.DateTime(timezone=True), nullable=False, server_default=db.func.now()
    )
    entity_type = db.Column(db.String(64), nullable=False)
    entity_id = db.Column(db.BigInteger)
    action = db.Column(db.String(10), nullable=False)
    actor_user_id = db.Column(db.Integer)
    actor_label = db.Column(db.String(150), nullable=False)
    channel = db.Column(db.String(10), nullable=False)
    before = db.Column(db.JSON().with_variant(JSONB, 'postgresql'))
    after = db.Column(db.JSON().with_variant(JSONB, 'postgresql'))
    request_id = db.Column(db.String(64))

    def __repr__(self):
        return f'<AuditLog {self.action} {self.entity_type}#{self.entity_id}>'


class ApiToken(db.Model):
    """Revocable, expiring bearer token that lets an adapter act as a user.

    Only ``HMAC-SHA256(key, token)`` is stored (``token_hash``); the plaintext
    exists once, at issue time. The table is deliberately not in the audited
    registry: ``last_used_at`` changes on authentication without an audit row,
    so ``services.api_tokens`` audits issue and revoke explicitly.
    """

    __tablename__ = 'api_tokens'
    __table_args__ = (
        db.PrimaryKeyConstraint('id', name='pk_api_tokens'),
        db.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name='fk_api_tokens_user_id_users',
            ondelete='CASCADE',
        ),
        db.UniqueConstraint('prefix', name='uq_api_tokens_prefix'),
        db.Index('ix_api_tokens_user_id', 'user_id'),
    )

    id = db.Column(db.Integer)
    user_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    prefix = db.Column(db.String(8), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False)
    scopes = db.Column(db.String(32), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_by_label = db.Column(db.String(150), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_by_label = db.Column(db.String(150), nullable=True)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f'<ApiToken {self.prefix} user={self.user_id}>'
