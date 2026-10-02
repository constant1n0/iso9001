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

"""Document service, following ``nonconformities``.

Authorization comes from ``policy`` (resource ``DOCUMENTS``); validation mirrors
``DocumentForm``. ``code`` is unique: a duplicate raises ``Conflict``. There is
no versioning or approval workflow yet (Wave 1); ``signature`` is not writable.
"""

from __future__ import annotations


from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document
from . import policy
from .actor import Actor
from .errors import NotFound
from .policy import Action, Resource


def get(session: Session, actor: Actor, document_id: int) -> Document:
    """Return one document or raise ``NotFound``."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    return _load(session, document_id)


def _load(session: Session, document_id: int) -> Document:
    found = session.get(Document, document_id)
    if found is None:
        raise NotFound("Documento no encontrado.")
    return found


def list_(session: Session, actor: Actor) -> list[Document]:
    """All documents ordered by code."""
    policy.require(actor, Action.READ, Resource.DOCUMENTS)
    return list(session.scalars(select(Document).order_by(Document.code)))
