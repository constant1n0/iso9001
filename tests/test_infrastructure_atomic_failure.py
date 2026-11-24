"""S3 and Assumptions: a failed history append must not advance the plan."""
from datetime import date
from unittest.mock import patch
from test_nonconformity_service import ServiceBase, actor
from app.extensions import db
from app.models import RoleEnum
from app.services import infrastructure as infra


class InfrastructureAtomicFailure(ServiceBase):
    def test_failed_append_rolls_back_parent_and_audit(self):
        who = actor(RoleEnum.ADMINISTRADOR)
        day, next_day = date(2020, 1, 1), date(2020, 2, 1)
        item = infra.items.create(db.session, who, dict(
            codigo="TX-1", nombre="Servidor", tipo="tic",
            frecuencia="Mensual", fecha_proxima=day))
        db.session.commit()
        item_id, count = item.id, len(self.audit_rows())
        data = dict(elemento_id=item_id, fecha=day, tipo="preventivo",
                    resultado="conforme", realizado_por="SAT", evidencia="Parte",
                    fecha_proxima=next_day)
        with patch("app.services.infrastructure.crud.create",
                   side_effect=RuntimeError("append failed")):
            with self.assertRaisesRegex(RuntimeError, "append failed"):
                infra.maintenance.create(db.session, who, data, today=day)
        db.session.rollback()
        self.assertEqual(day, infra.items.get(db.session, who, item_id).fecha_proxima)
        self.assertEqual([], infra.maintenance.list_(db.session, who))
        self.assertEqual(count, len(self.audit_rows()))
