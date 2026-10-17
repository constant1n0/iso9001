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


class ResultadoVerificacion(enum.Enum):
    """Outcome of verifying a corrective action's effectiveness (decision N3)."""

    eficaz = 'Eficaz'
    no_eficaz = 'No eficaz'


class EstadoAccionCorrectiva(enum.Enum):
    """Where a corrective action stands; derived from its dates and verification."""

    planificada = 'Planificada'
    realizada = 'Realizada'
    verificada_eficaz = 'Verificada eficaz'
    verificada_no_eficaz = 'Verificada no eficaz'


class AccionCorrectiva(RecordMetadataMixin, db.Model):
    """A corrective action taken on a nonconformity (ISO 9001 clause 10.2, decision N3).

    Its status is derived (``estado``), never stored. The verification columns
    are written only by ``services.corrective_actions.verify``; the service
    also keeps the nonconformity's state in step with its actions (N5).
    """

    __tablename__ = 'acciones_correctivas'
    __table_args__ = (
        db.ForeignKeyConstraint(
            ['no_conformidad_id'],
            ['no_conformidades.id'],
            name='fk_acciones_correctivas_no_conformidad_id_no_conformidades',
            ondelete='CASCADE',
        ),
        db.ForeignKeyConstraint(
            ['responsable_id'],
            ['personas.id'],
            name='fk_acciones_correctivas_responsable_id_personas',
            ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['verificador_id'],
            ['personas.id'],
            name='fk_acciones_correctivas_verificador_id_personas',
            ondelete='RESTRICT',
        ),
        db.Index('ix_acciones_correctivas_no_conformidad_id', 'no_conformidad_id'),
        db.Index('ix_acciones_correctivas_responsable_id', 'responsable_id'),
        db.Index('ix_acciones_correctivas_verificador_id', 'verificador_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    no_conformidad_id = db.Column(db.Integer, nullable=False)
    descripcion = db.Column(db.String(1000), nullable=False)
    responsable_id = db.Column(db.Integer, nullable=False)
    fecha_prevista = db.Column(db.Date, nullable=False)
    fecha_realizada = db.Column(db.Date)
    resultado_verificacion = db.Column(
        text_enum(ResultadoVerificacion, 'ck_acciones_correctivas_resultado_verificacion')
    )
    fecha_verificacion = db.Column(db.Date)
    verificador_id = db.Column(db.Integer, nullable=True)
    evidencia_verificacion = db.Column(db.Text)

    @property
    def estado(self) -> EstadoAccionCorrectiva:
        """Planificada until done, then Realizada until verified, then the result."""
        if self.resultado_verificacion is ResultadoVerificacion.eficaz:
            return EstadoAccionCorrectiva.verificada_eficaz
        if self.resultado_verificacion is ResultadoVerificacion.no_eficaz:
            return EstadoAccionCorrectiva.verificada_no_eficaz
        if self.fecha_realizada is not None:
            return EstadoAccionCorrectiva.realizada
        return EstadoAccionCorrectiva.planificada

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

class EstadoProgramaAuditoria(enum.Enum):
    """Where a yearly audit programme stands (decision AP1 of ``audit-programme``).

    Only ``services.audit_programmes.approve`` moves it, and only forward.
    """

    borrador = 'Borrador'
    aprobado = 'Aprobado'


# An approved programme records when and by whom; a draft records neither.
_PROGRAMME_APPROVAL = (
    "(estado = 'borrador' AND aprobado_en IS NULL AND aprobado_por_id IS NULL) OR "
    "(estado = 'aprobado' AND aprobado_en IS NOT NULL AND aprobado_por_id IS NOT NULL)"
)


class ProgramaAuditoria(RecordMetadataMixin, db.Model):
    """The internal audit programme of one year (ISO 9001 clause 9.2.2, decision AP1).

    One per year, with objectives and scope. An administrator approves a
    draft, recording the date and the approving person; an approved
    programme's year never changes (``services.audit_programmes``).
    """

    __tablename__ = 'programas_auditoria'
    __table_args__ = (
        db.UniqueConstraint('anio', name='uq_programas_auditoria_anio'),
        db.CheckConstraint('anio BETWEEN 2000 AND 2100', name='ck_programas_auditoria_anio'),
        db.CheckConstraint(_PROGRAMME_APPROVAL, name='ck_programas_auditoria_aprobacion'),
        db.ForeignKeyConstraint(
            ['aprobado_por_id'], ['personas.id'],
            name='fk_programas_auditoria_aprobado_por_id_personas', ondelete='RESTRICT',
        ),
        db.Index('ix_programas_auditoria_aprobado_por_id', 'aprobado_por_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    anio = db.Column(db.Integer, nullable=False)
    objetivos = db.Column(db.Text, nullable=False)
    alcance = db.Column(db.Text, nullable=False)
    estado = db.Column(
        text_enum(EstadoProgramaAuditoria, 'ck_programas_auditoria_estado'),
        nullable=False,
        default=EstadoProgramaAuditoria.borrador,
        server_default=EstadoProgramaAuditoria.borrador.name,
    )
    aprobado_en = db.Column(db.Date)
    aprobado_por_id = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f'<ProgramaAuditoria {self.anio}>'


# The audit team (decision AP2); a plain table, not audited itself: team
# changes are recorded on the audit's own rows (``equipo_ids``).
auditoria_equipo = db.Table(
    'auditoria_equipo',
    db.Column('auditoria_id', db.Integer, nullable=False),
    db.Column('persona_id', db.Integer, nullable=False),
    db.PrimaryKeyConstraint('auditoria_id', 'persona_id', name='pk_auditoria_equipo'),
    db.ForeignKeyConstraint(
        ['auditoria_id'], ['auditorias.id'],
        name='fk_auditoria_equipo_auditoria_id_auditorias', ondelete='CASCADE',
    ),
    db.ForeignKeyConstraint(
        ['persona_id'], ['personas.id'],
        name='fk_auditoria_equipo_persona_id_personas', ondelete='RESTRICT',
    ),
    db.Index('ix_auditoria_equipo_persona_id', 'persona_id'),
)


# Modelo para Auditorías
class Auditoria(RecordMetadataMixin, db.Model):
    """An internal audit (ISO 9001 clause 9.2).

    The planning columns (decision AP2) are nullable so audits that predate
    them stay valid: the programme it belongs to, its scope and criteria, the
    audited-area owner and the team. ``auditor_id`` is the lead auditor.
    """

    __tablename__ = 'auditorias'
    id = db.Column(db.Integer, primary_key=True)
    area_auditada = db.Column(db.String(50), nullable=False)
    fecha = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    auditor = db.Column(db.String(50), nullable=False)  # legacy free text
    auditor_id = person_link('auditorias', 'auditor_id')
    resultado = db.Column(db.Text, nullable=False)
    accion_correctiva = db.Column(db.Text)
    estado = db.Column(db.Enum(EstadoAuditoriaEnum), nullable=False, default=EstadoAuditoriaEnum.PENDIENTE)
    programa_id = db.Column(
        db.Integer,
        db.ForeignKey('programas_auditoria.id',
                      name='fk_auditorias_programa_id_programas_auditoria', ondelete='RESTRICT'),
        nullable=True,
        index=True,
    )
    alcance = db.Column(db.Text)
    criterios = db.Column(db.Text)  # e.g. "ISO 9001:2026, procedimientos internos"
    responsable_area_id = person_link('auditorias', 'responsable_area_id')

    # One-way on purpose, like ``Person.roles``: a back-reference would mark
    # people as changed (and unaudited) whenever a team changes.
    equipo = db.relationship(
        'Person',
        secondary=auditoria_equipo,
        order_by='Person.nombre',
        lazy='selectin',
    )

    @property
    def equipo_ids(self) -> list[int]:
        """Ids of the team members, ascending."""
        return sorted(person.id for person in self.equipo)

    def __repr__(self):
        return f'<Auditoria {self.area_auditada}>'


class TipoHallazgo(enum.Enum):
    """How an audit finding is classified (decision AP4 of ``audit-programme``)."""

    nc_mayor = 'No conformidad mayor'
    nc_menor = 'No conformidad menor'
    observacion = 'Observación'
    oportunidad_mejora = 'Oportunidad de mejora'


class HallazgoAuditoria(RecordMetadataMixin, db.Model):
    """A finding of an internal audit (ISO 9001 clause 9.2.2, decisions AP4 and AP5).

    A nonconformity finding gets its own ``NoConformidad`` when it is recorded
    (``services.audit_findings``) and keeps the link, at most one finding per
    nonconformity. An audit with findings cannot be deleted; deleting the
    nonconformity clears the link.
    """

    __tablename__ = 'hallazgos_auditoria'
    __table_args__ = (
        db.ForeignKeyConstraint(
            ['auditoria_id'], ['auditorias.id'],
            name='fk_hallazgos_auditoria_auditoria_id_auditorias', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['no_conformidad_id'], ['no_conformidades.id'],
            name='fk_hallazgos_auditoria_no_conformidad_id_no_conformidades',
            ondelete='SET NULL',
        ),
        db.UniqueConstraint('no_conformidad_id', name='uq_hallazgos_auditoria_no_conformidad_id'),
        db.Index('ix_hallazgos_auditoria_auditoria_id', 'auditoria_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    auditoria_id = db.Column(db.Integer, nullable=False)
    tipo = db.Column(text_enum(TipoHallazgo, 'ck_hallazgos_auditoria_tipo'), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    clausula = db.Column(db.String(20))  # ISO 9001 clause, e.g. "8.5.1"
    evidencia = db.Column(db.Text)
    no_conformidad_id = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f'<HallazgoAuditoria {self.id} {self.tipo.name if self.tipo else None}>'


class SentidoObjetivo(enum.Enum):
    """How a quality objective's target is met (decision QO1): at or above, or at or below."""

    mayor_igual = '≥'
    menor_igual = '≤'


class FrecuenciaMedicion(enum.Enum):
    """How often a quality objective is measured (decisions QO1 and QO3)."""

    mensual = 'Mensual'
    trimestral = 'Trimestral'
    semestral = 'Semestral'
    anual = 'Anual'


class EstadoObjetivo(enum.Enum):
    """Where a quality objective stands (decision QO1); measurements need ``activo``."""

    activo = 'Activo'
    alcanzado = 'Alcanzado'
    no_alcanzado = 'No alcanzado'
    cancelado = 'Cancelado'


class ObjetivoCalidad(RecordMetadataMixin, db.Model):
    """A measurable quality objective (ISO 9001 clause 6.2, decision QO1 of ``quality-objectives``).

    ``meta`` is met when the indicator's value is at or above it (``mayor_igual``)
    or at or below it (``menor_igual``). Its measurements (``MedicionObjetivo``)
    keep it from being deleted, and ``services.quality_objectives`` fixes the
    target and its direction once one exists.
    """

    __tablename__ = 'objetivos_calidad'
    __table_args__ = (
        db.CheckConstraint('anio BETWEEN 2000 AND 2100', name='ck_objetivos_calidad_anio'),
        db.CheckConstraint('fecha_limite >= fecha_inicio', name='ck_objetivos_calidad_fechas'),
        db.ForeignKeyConstraint(
            ['proceso_id'], ['procesos_operacion.id_proceso'],
            name='fk_objetivos_calidad_proceso_id_procesos_operacion', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['responsable_id'], ['personas.id'],
            name='fk_objetivos_calidad_responsable_id_personas', ondelete='RESTRICT',
        ),
        db.Index('ix_objetivos_calidad_proceso_id', 'proceso_id'),
        db.Index('ix_objetivos_calidad_responsable_id', 'responsable_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text)
    proceso_id = db.Column(db.Integer, nullable=True)
    indicador = db.Column(db.String(200), nullable=False)
    unidad = db.Column(db.String(30))
    meta = db.Column(db.Numeric(12, 4), nullable=False)
    sentido = db.Column(text_enum(SentidoObjetivo, 'ck_objetivos_calidad_sentido'), nullable=False)
    valor_inicial = db.Column(db.Numeric(12, 4))
    responsable_id = db.Column(db.Integer, nullable=False)
    anio = db.Column(db.Integer, nullable=False)
    fecha_inicio = db.Column(db.Date, nullable=False)
    fecha_limite = db.Column(db.Date, nullable=False)
    frecuencia = db.Column(
        text_enum(FrecuenciaMedicion, 'ck_objetivos_calidad_frecuencia'), nullable=False
    )
    recursos = db.Column(db.Text)
    evaluacion = db.Column(db.Text)  # how the results are evaluated (6.2.2 e)
    estado = db.Column(
        text_enum(EstadoObjetivo, 'ck_objetivos_calidad_estado'),
        nullable=False,
        default=EstadoObjetivo.activo,
        server_default=EstadoObjetivo.activo.name,
    )

    def __repr__(self):
        return f'<ObjetivoCalidad {self.id} {self.titulo}>'


class MedicionObjetivo(RecordMetadataMixin, db.Model):
    """One measurement of a quality objective's indicator (decision QO2), one per date."""

    __tablename__ = 'mediciones_objetivo'
    __table_args__ = (
        db.UniqueConstraint(
            'objetivo_id', 'fecha', name='uq_mediciones_objetivo_objetivo_id_fecha'
        ),
        db.ForeignKeyConstraint(
            ['objetivo_id'], ['objetivos_calidad.id'],
            name='fk_mediciones_objetivo_objetivo_id_objetivos_calidad', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['registrado_por_id'], ['personas.id'],
            name='fk_mediciones_objetivo_registrado_por_id_personas', ondelete='RESTRICT',
        ),
        db.Index('ix_mediciones_objetivo_objetivo_id', 'objetivo_id'),
        db.Index('ix_mediciones_objetivo_registrado_por_id', 'registrado_por_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    objetivo_id = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Numeric(12, 4), nullable=False)
    comentario = db.Column(db.Text)
    registrado_por_id = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f'<MedicionObjetivo {self.objetivo_id} {self.fecha}>'


class EstadoRevisionDireccion(enum.Enum):
    """Where a management review stands (decisions MR1 and MR4 of ``management-review``).

    Only ``services.management_reviews.hold`` and ``close`` move it, forward only.
    """

    borrador = 'Borrador'
    celebrada = 'Celebrada'
    cerrada = 'Cerrada'


class TipoAccionRevision(enum.Enum):
    """What a management review output is about (ISO 9001 clause 9.3.3, decision MR3)."""

    mejora = 'Oportunidad de mejora'
    cambio_sgc = 'Cambio en el SGC'
    recursos = 'Necesidad de recursos'


class EstadoAccionRevision(enum.Enum):
    """Where a management review action stands (decision MR3)."""

    pendiente = 'Pendiente'
    en_curso = 'En curso'
    completada = 'Completada'
    cancelada = 'Cancelada'


# MR1: the period ends on or before the meeting. MR2: a review holds its frozen
# inputs from the moment it is held. MR4: only a closed review has a closing date.
_REVIEW_PERIOD = 'periodo_desde <= periodo_hasta AND periodo_hasta <= fecha'
_REVIEW_INPUTS = (
    "(estado = 'borrador' AND datos_entrada IS NULL) OR "
    "(estado <> 'borrador' AND datos_entrada IS NOT NULL)"
)
_REVIEW_CLOSING = (
    "(estado = 'cerrada' AND fecha_cierre IS NOT NULL) OR "
    "(estado <> 'cerrada' AND fecha_cierre IS NULL)"
)
# MR3: a completed action records when, and only a completed one.
_ACTION_COMPLETION = (
    "(estado = 'completada' AND fecha_completada IS NOT NULL) OR "
    "(estado <> 'completada' AND fecha_completada IS NULL)"
)

# The attendees of a review; a plain table, not audited itself: attendance
# changes are recorded on the review's own rows (``asistente_ids``).
revision_asistentes = db.Table(
    'revision_asistentes',
    db.Column('revision_id', db.Integer, nullable=False),
    db.Column('persona_id', db.Integer, nullable=False),
    db.PrimaryKeyConstraint('revision_id', 'persona_id', name='pk_revision_asistentes'),
    db.ForeignKeyConstraint(
        ['revision_id'], ['revisiones_direccion.id'],
        name='fk_revision_asistentes_revision_id_revisiones_direccion', ondelete='CASCADE',
    ),
    db.ForeignKeyConstraint(
        ['persona_id'], ['personas.id'],
        name='fk_revision_asistentes_persona_id_personas', ondelete='RESTRICT',
    ),
    db.Index('ix_revision_asistentes_persona_id', 'persona_id'),
)


class RevisionDireccion(RecordMetadataMixin, db.Model):
    """A management review of the QMS (ISO 9001 clause 9.3, decisions MR1 to MR6).

    A meeting date, the period it reviews, its attendees, a comment per input
    of 9.3.2 and the conclusions. ``datos_entrada`` is the input summary frozen
    when the review is held (``services.management_reviews.hold``), so the
    minutes keep what was presented; ``None`` while a draft. ``None`` is
    stored as SQL NULL, never as JSON ``null``, so the CHECK can see it.
    """

    __tablename__ = 'revisiones_direccion'
    __table_args__ = (
        db.CheckConstraint(_REVIEW_PERIOD, name='ck_revisiones_direccion_periodo'),
        db.CheckConstraint(_REVIEW_INPUTS, name='ck_revisiones_direccion_datos_entrada'),
        db.CheckConstraint(_REVIEW_CLOSING, name='ck_revisiones_direccion_cierre'),
        db.ForeignKeyConstraint(
            ['celebrada_por_id'], ['personas.id'],
            name='fk_revisiones_direccion_celebrada_por_id_personas', ondelete='RESTRICT',
        ),
        db.Index('ix_revisiones_direccion_celebrada_por_id', 'celebrada_por_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False)  # the meeting
    periodo_desde = db.Column(db.Date, nullable=False)
    periodo_hasta = db.Column(db.Date, nullable=False)
    estado = db.Column(
        text_enum(EstadoRevisionDireccion, 'ck_revisiones_direccion_estado'),
        nullable=False,
        default=EstadoRevisionDireccion.borrador,
        server_default=EstadoRevisionDireccion.borrador.name,
    )
    comentario_acciones_previas = db.Column(db.Text)  # 9.3.2 a
    comentario_contexto = db.Column(db.Text)  # 9.3.2 b: issues, stakeholders, risks
    comentario_desempeno = db.Column(db.Text)  # 9.3.2 c, including external providers
    comentario_recursos = db.Column(db.Text)  # 9.3.2 d
    comentario_riesgos = db.Column(db.Text)  # 9.3.2 e: actions on risks and opportunities
    comentario_mejora = db.Column(db.Text)  # 9.3.2 f
    conclusiones = db.Column(db.Text)  # suitability, adequacy and effectiveness of the QMS
    datos_entrada = db.Column(
        db.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), 'postgresql')
    )
    celebrada_por_id = db.Column(db.Integer, nullable=True)
    fecha_cierre = db.Column(db.Date)

    # One-way on purpose, like ``Auditoria.equipo``: a back-reference would mark
    # people as changed (and unaudited) whenever the attendance changes.
    asistentes = db.relationship(
        'Person',
        secondary=revision_asistentes,
        order_by='Person.nombre',
        lazy='selectin',
    )

    @property
    def asistente_ids(self) -> list[int]:
        """Ids of the attendees, ascending."""
        return sorted(person.id for person in self.asistentes)

    def __repr__(self):
        return f'<RevisionDireccion {self.id} {self.fecha}>'


class AccionRevision(RecordMetadataMixin, db.Model):
    """An output of a management review (ISO 9001 clause 9.3.3, decision MR3).

    An owner, a deadline and its follow-up. Once its review is closed only
    ``estado``, ``fecha_completada`` and ``resultado`` change
    (``services.management_reviews``); a review with actions cannot be deleted.
    """

    __tablename__ = 'acciones_revision'
    __table_args__ = (
        db.CheckConstraint(_ACTION_COMPLETION, name='ck_acciones_revision_completada'),
        db.ForeignKeyConstraint(
            ['revision_id'], ['revisiones_direccion.id'],
            name='fk_acciones_revision_revision_id_revisiones_direccion', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['responsable_id'], ['personas.id'],
            name='fk_acciones_revision_responsable_id_personas', ondelete='RESTRICT',
        ),
        db.Index('ix_acciones_revision_revision_id', 'revision_id'),
        db.Index('ix_acciones_revision_responsable_id', 'responsable_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    revision_id = db.Column(db.Integer, nullable=False)
    tipo = db.Column(text_enum(TipoAccionRevision, 'ck_acciones_revision_tipo'), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    responsable_id = db.Column(db.Integer, nullable=False)
    fecha_limite = db.Column(db.Date, nullable=False)
    estado = db.Column(
        text_enum(EstadoAccionRevision, 'ck_acciones_revision_estado'),
        nullable=False,
        default=EstadoAccionRevision.pendiente,
        server_default=EstadoAccionRevision.pendiente.name,
    )
    fecha_completada = db.Column(db.Date)
    resultado = db.Column(db.Text)

    def __repr__(self):
        return f'<AccionRevision {self.id} {self.estado.name if self.estado else None}>'


class OrigenRiesgo(enum.Enum):
    """Whether a risk or an opportunity arises inside or outside the organization (RO1)."""

    interno = 'Interno'
    externo = 'Externo'


class TratamientoRiesgo(enum.Enum):
    """How a risk is treated (ISO 9001 clause 6.1.2, decision RO1)."""

    aceptar = 'Aceptar'
    mitigar = 'Mitigar'
    transferir = 'Transferir'
    evitar = 'Evitar'


class EstadoRiesgo(enum.Enum):
    """Where a risk stands (decision RO1)."""

    identificado = 'Identificado'
    en_tratamiento = 'En tratamiento'
    controlado = 'Controlado'
    cerrado = 'Cerrado'


class DecisionOportunidad(enum.Enum):
    """What is decided about an opportunity (decision RO2)."""

    aprovechar = 'Aprovechar'
    posponer = 'Posponer'
    descartar = 'Descartar'


class EstadoOportunidad(enum.Enum):
    """Where an opportunity stands (decision RO2)."""

    identificada = 'Identificada'
    en_curso = 'En curso'
    aprovechada = 'Aprovechada'
    descartada = 'Descartada'


class EstadoAccionRiesgo(enum.Enum):
    """Where an action on a risk or an opportunity stands (decision RO3)."""

    pendiente = 'Pendiente'
    en_curso = 'En curso'
    completada = 'Completada'
    cancelada = 'Cancelada'


class EficaciaAccion(enum.Enum):
    """Result of evaluating a completed action's effectiveness (6.1.2 b, decision RO3)."""

    eficaz = 'Eficaz'
    no_eficaz = 'No eficaz'


class NivelRiesgo(enum.Enum):
    """A risk's level, from the product of likelihood and impact (decision RO1)."""

    bajo = 'Bajo'
    medio = 'Medio'
    alto = 'Alto'
    critico = 'Crítico'


class PrioridadOportunidad(enum.Enum):
    """An opportunity's priority, from the product of benefit and feasibility (RO2)."""

    baja = 'Baja'
    media = 'Media'
    alta = 'Alta'


ESCALA_MIN, ESCALA_MAX = 1, 5  # every assessment scale of the registers (RO1, RO2)


def _scale_product(first: int | None, second: int | None) -> int | None:
    """Product of a 1-5 pair, ``None`` for a pending assessment (both ``None``, RO4).

    Raises ``ValueError`` for half an assessment or a value off the scale; the
    registers' CHECK constraints refuse the same.
    """
    if first is None and second is None:
        return None
    for value in (first, second):
        if (isinstance(value, bool) or not isinstance(value, int)
                or not ESCALA_MIN <= value <= ESCALA_MAX):
            raise ValueError(f'Assessment values must be integers from {ESCALA_MIN} to '
                             f'{ESCALA_MAX}, both given or both None: {first!r}, {second!r}')
    return first * second


def nivel(probabilidad: int | None, impacto: int | None) -> NivelRiesgo | None:
    """A risk's level: ``bajo`` 1-4, ``medio`` 5-9, ``alto`` 10-16, ``critico`` 20-25.

    ``None`` while the risk is not assessed yet (decision RO4).

    Raises:
        ValueError: half an assessment, or a value that is not an integer from 1 to 5.
    """
    product = _scale_product(probabilidad, impacto)
    if product is None:
        return None
    if product <= 4:
        return NivelRiesgo.bajo
    if product <= 9:
        return NivelRiesgo.medio
    if product <= 16:
        return NivelRiesgo.alto
    return NivelRiesgo.critico


def prioridad(beneficio: int | None, viabilidad: int | None) -> PrioridadOportunidad | None:
    """An opportunity's priority: ``baja`` 1-6, ``media`` 8-12, ``alta`` 15-25.

    ``None`` while the opportunity is not assessed yet (decision RO4).

    Raises:
        ValueError: half an assessment, or a value that is not an integer from 1 to 5.
    """
    product = _scale_product(beneficio, viabilidad)
    if product is None:
        return None
    if product <= 6:
        return PrioridadOportunidad.baja
    if product <= 12:
        return PrioridadOportunidad.media
    return PrioridadOportunidad.alta


def _scale_pair(first: str, second: str) -> str:
    """CHECK: both columns on the 1-5 scale, or both NULL (assessment pending, RO4).

    Spelled out with ``IS NOT NULL``: a CHECK whose expression is NULL passes.
    """
    return (f'({first} IS NULL AND {second} IS NULL) OR '
            f'({first} IS NOT NULL AND {second} IS NOT NULL AND '
            f'{first} BETWEEN {ESCALA_MIN} AND {ESCALA_MAX} AND '
            f'{second} BETWEEN {ESCALA_MIN} AND {ESCALA_MAX})')


# RO3: an action belongs to exactly one risk or one opportunity, and its
# effectiveness (result and date together) is evaluated only once completed.
_ONE_REGISTER = ('(riesgo_id IS NOT NULL AND oportunidad_id IS NULL) OR '
                 '(riesgo_id IS NULL AND oportunidad_id IS NOT NULL)')
_EVALUATION = ("(eficacia IS NULL AND fecha_evaluacion IS NULL) OR "
               "(eficacia IS NOT NULL AND fecha_evaluacion IS NOT NULL "
               "AND estado = 'completada')")


def _register_links(table: str) -> tuple:
    """Interested party, process and owner of a risk or an opportunity, all optional.

    ``ON DELETE RESTRICT`` keeps a cited party, process or person; the owner is
    NULL only on rows migrated from ``riesgos_oportunidades`` (the services
    require it for new records).
    """
    return (
        db.ForeignKeyConstraint(
            ['parte_interesada_id'], ['partes_interesadas.id_interesado'],
            name=f'fk_{table}_parte_interesada_id_partes_interesadas', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['proceso_id'], ['procesos_operacion.id_proceso'],
            name=f'fk_{table}_proceso_id_procesos_operacion', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['responsable_id'], ['personas.id'],
            name=f'fk_{table}_responsable_id_personas', ondelete='RESTRICT',
        ),
        db.Index(f'ix_{table}_parte_interesada_id', 'parte_interesada_id'),
        db.Index(f'ix_{table}_proceso_id', 'proceso_id'),
        db.Index(f'ix_{table}_responsable_id', 'responsable_id'),
    )


class Riesgo(RecordMetadataMixin, db.Model):
    """A risk of the QMS (ISO 9001 clause 6.1, decisions RO1 and RO4 of ``risks-opportunities``).

    Assessed by likelihood and impact (1-5) before its actions and again after
    them (``*_residual``); a pair is either complete or still pending. The level
    is computed (``nivel``), never stored. Its actions (``AccionRiesgo``) keep
    it from being deleted.
    """

    __tablename__ = 'riesgos'
    __table_args__ = (
        db.CheckConstraint(_scale_pair('probabilidad', 'impacto'),
                           name='ck_riesgos_valoracion'),
        db.CheckConstraint(_scale_pair('probabilidad_residual', 'impacto_residual'),
                           name='ck_riesgos_valoracion_residual'),
        *_register_links('riesgos'),
    )

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    origen = db.Column(text_enum(OrigenRiesgo, 'ck_riesgos_origen'), nullable=False)
    parte_interesada_id = db.Column(db.Integer, nullable=True)
    proceso_id = db.Column(db.Integer, nullable=True)
    responsable_id = db.Column(db.Integer, nullable=True)
    probabilidad = db.Column(db.Integer)
    impacto = db.Column(db.Integer)
    tratamiento = db.Column(text_enum(TratamientoRiesgo, 'ck_riesgos_tratamiento'))
    probabilidad_residual = db.Column(db.Integer)
    impacto_residual = db.Column(db.Integer)
    estado = db.Column(
        text_enum(EstadoRiesgo, 'ck_riesgos_estado'),
        nullable=False,
        default=EstadoRiesgo.identificado,
        server_default=EstadoRiesgo.identificado.name,
    )
    fecha_identificacion = db.Column(db.Date, nullable=False)
    fecha_revision = db.Column(db.Date)  # the next review
    notas = db.Column(db.Text)

    @property
    def nivel(self) -> NivelRiesgo | None:
        """Level before the actions; ``None`` while not assessed."""
        return nivel(self.probabilidad, self.impacto)

    @property
    def nivel_residual(self) -> NivelRiesgo | None:
        """Level after the actions; ``None`` while not assessed."""
        return nivel(self.probabilidad_residual, self.impacto_residual)

    def __repr__(self):
        return f'<Riesgo {self.id} {self.titulo}>'


class Oportunidad(RecordMetadataMixin, db.Model):
    """An opportunity for the QMS (ISO 9001 clause 6.1, decisions RO2 and RO4).

    The frame of ``Riesgo`` with benefit and feasibility (1-5) instead of
    likelihood and impact, a decision instead of a treatment, and no residual
    assessment. The priority is computed (``prioridad``), never stored.
    """

    __tablename__ = 'oportunidades'
    __table_args__ = (
        db.CheckConstraint(_scale_pair('beneficio', 'viabilidad'),
                           name='ck_oportunidades_valoracion'),
        *_register_links('oportunidades'),
    )

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    origen = db.Column(text_enum(OrigenRiesgo, 'ck_oportunidades_origen'), nullable=False)
    parte_interesada_id = db.Column(db.Integer, nullable=True)
    proceso_id = db.Column(db.Integer, nullable=True)
    responsable_id = db.Column(db.Integer, nullable=True)
    beneficio = db.Column(db.Integer)
    viabilidad = db.Column(db.Integer)
    decision = db.Column(text_enum(DecisionOportunidad, 'ck_oportunidades_decision'))
    estado = db.Column(
        text_enum(EstadoOportunidad, 'ck_oportunidades_estado'),
        nullable=False,
        default=EstadoOportunidad.identificada,
        server_default=EstadoOportunidad.identificada.name,
    )
    fecha_identificacion = db.Column(db.Date, nullable=False)
    fecha_revision = db.Column(db.Date)  # the next review
    notas = db.Column(db.Text)

    @property
    def prioridad(self) -> PrioridadOportunidad | None:
        """Priority from benefit and feasibility; ``None`` while not assessed."""
        return prioridad(self.beneficio, self.viabilidad)

    def __repr__(self):
        return f'<Oportunidad {self.id} {self.titulo}>'


class AccionRiesgo(RecordMetadataMixin, db.Model):
    """An action on exactly one risk or one opportunity (6.1.2, decision RO3).

    An owner, a deadline and its follow-up; a completed action records when,
    and only then is its effectiveness evaluated (result and date together).
    """

    __tablename__ = 'acciones_riesgo'
    __table_args__ = (
        db.CheckConstraint(_ONE_REGISTER, name='ck_acciones_riesgo_un_registro'),
        db.CheckConstraint(_ACTION_COMPLETION, name='ck_acciones_riesgo_completada'),
        db.CheckConstraint(_EVALUATION, name='ck_acciones_riesgo_evaluacion'),
        db.ForeignKeyConstraint(
            ['riesgo_id'], ['riesgos.id'],
            name='fk_acciones_riesgo_riesgo_id_riesgos', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['oportunidad_id'], ['oportunidades.id'],
            name='fk_acciones_riesgo_oportunidad_id_oportunidades', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['responsable_id'], ['personas.id'],
            name='fk_acciones_riesgo_responsable_id_personas', ondelete='RESTRICT',
        ),
        db.Index('ix_acciones_riesgo_riesgo_id', 'riesgo_id'),
        db.Index('ix_acciones_riesgo_oportunidad_id', 'oportunidad_id'),
        db.Index('ix_acciones_riesgo_responsable_id', 'responsable_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    riesgo_id = db.Column(db.Integer, nullable=True)
    oportunidad_id = db.Column(db.Integer, nullable=True)
    descripcion = db.Column(db.Text, nullable=False)
    responsable_id = db.Column(db.Integer, nullable=False)
    fecha_limite = db.Column(db.Date, nullable=False)
    estado = db.Column(
        text_enum(EstadoAccionRiesgo, 'ck_acciones_riesgo_estado'),
        nullable=False,
        default=EstadoAccionRiesgo.pendiente,
        server_default=EstadoAccionRiesgo.pendiente.name,
    )
    fecha_completada = db.Column(db.Date)
    eficacia = db.Column(text_enum(EficaciaAccion, 'ck_acciones_riesgo_eficacia'))
    fecha_evaluacion = db.Column(db.Date)
    comentario_eficacia = db.Column(db.Text)

    def __repr__(self):
        return f'<AccionRiesgo {self.id} {self.estado.name if self.estado else None}>'

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
    """A controlled document (ISO 9001 clause 7.5, ``document-control``).

    It keeps its identity, owner and next review date (decision DC5); its text
    lives in numbered revisions (``DocumentRevision``). Documents are withdrawn
    (``withdrawn_*``, DC6), never deleted, and a withdrawn one is read-only.
    ``owner_id`` is required by the service; documents older than it keep none.
    """

    __tablename__ = 'documents'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(50), nullable=False, unique=True, index=True)
    category = db.Column(db.Enum(DocumentCategory), nullable=False)
    owner_id = person_link('documents', 'owner_id')
    next_review_date = db.Column(db.Date)
    withdrawn_at = db.Column(db.Date)
    withdrawn_reason = db.Column(db.Text)
    withdrawn_by_id = person_link('documents', 'withdrawn_by_id')

    def __repr__(self):
        return f'<Document {self.code}>'


class EstadoRevision(enum.Enum):
    """Where a document revision stands (decision DC2 of ``document-control``).

    Only ``services.document_revisions`` changes it; ``vigente`` and
    ``obsoleto`` revisions never change again (DC3).
    """

    borrador = 'Borrador'
    en_revision = 'En revisión'
    aprobado = 'Aprobado'
    vigente = 'Vigente'
    obsoleto = 'Obsoleto'


# DC1: at most one revision in preparation and one in force per document.
_PENDING_REVISION = db.text("estado IN ('borrador', 'en_revision', 'aprobado')")
_EFFECTIVE_REVISION = db.text("estado = 'vigente'")
# An attachment is recorded whole (all five columns) or not at all (DC7). The
# services read this tuple too, so the columns are named in one place only.
ATTACHMENT_COLUMNS = ('attachment_name', 'attachment_path', 'attachment_size',
                      'attachment_sha256', 'attachment_mime')
_ATTACHMENT_COMPLETE = (
    '(' + ' AND '.join(f'{c} IS NULL' for c in ATTACHMENT_COLUMNS) + ') OR ('
    + ' AND '.join(f'{c} IS NOT NULL' for c in ATTACHMENT_COLUMNS) + ' AND attachment_size > 0)'
)


class DocumentRevision(RecordMetadataMixin, db.Model):
    """One numbered revision of a document's text (decisions DC1-DC3, DC7, DC8).

    ``author_id`` is required by the service; revisions converted from the
    documents that predate revisions have none and keep the old version text,
    approver and signature in the ``legacy_*`` columns. The optional attachment
    is a file in the document storage directory (``services.document_files``):
    ``attachment_path`` is its random stored name, unique so that no two
    revisions share a file, and the other ``attachment_*`` columns describe it.
    """

    __tablename__ = 'document_revisions'
    __table_args__ = (
        db.ForeignKeyConstraint(
            ['document_id'], ['documents.id'],
            name='fk_document_revisions_document_id_documents', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['author_id'], ['personas.id'],
            name='fk_document_revisions_author_id_personas', ondelete='RESTRICT',
        ),
        db.ForeignKeyConstraint(
            ['approver_id'], ['personas.id'],
            name='fk_document_revisions_approver_id_personas', ondelete='RESTRICT',
        ),
        db.UniqueConstraint('document_id', 'numero', name='uq_document_revisions_document_id_numero'),
        db.Index('ix_document_revisions_author_id', 'author_id'),
        db.Index('ix_document_revisions_approver_id', 'approver_id'),
        db.Index('uq_document_revisions_pending', 'document_id', unique=True,
                 postgresql_where=_PENDING_REVISION, sqlite_where=_PENDING_REVISION),
        db.Index('uq_document_revisions_effective', 'document_id', unique=True,
                 postgresql_where=_EFFECTIVE_REVISION, sqlite_where=_EFFECTIVE_REVISION),
        db.UniqueConstraint('attachment_path', name='uq_document_revisions_attachment_path'),
        db.CheckConstraint(_ATTACHMENT_COMPLETE, name='ck_document_revisions_attachment'),
    )

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    estado = db.Column(
        text_enum(EstadoRevision, 'ck_document_revisions_estado'),
        nullable=False,
        default=EstadoRevision.borrador,
        server_default=EstadoRevision.borrador.name,
    )
    content = db.Column(db.Text, nullable=False)
    change_summary = db.Column(db.Text)  # required to submit the revision for review
    author_id = db.Column(db.Integer, nullable=True)
    approver_id = db.Column(db.Integer, nullable=True)
    approved_at = db.Column(db.Date)
    effective_from = db.Column(db.Date)
    obsolete_from = db.Column(db.Date)
    review_comment = db.Column(db.Text)  # the latest rejection's reason
    legacy_version = db.Column(db.String(10))
    legacy_approved_by = db.Column(db.String(100))
    legacy_signature = db.Column(db.String(255))
    attachment_name = db.Column(db.String(255))  # sanitized display name
    attachment_path = db.Column(db.String(32))  # stored name: uuid4().hex, never a path
    attachment_size = db.Column(db.Integer)  # bytes
    attachment_sha256 = db.Column(db.String(64))  # hex digest
    attachment_mime = db.Column(db.String(100))  # detected from the content

    def __repr__(self):
        return f'<DocumentRevision {self.document_id}#{self.numero}>'


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
