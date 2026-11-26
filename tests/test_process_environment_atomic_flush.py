"""S5: failure after flushing history rolls back parent, evidence and audit."""
import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.extensions import db
from app.models import AuditLog, ComprobacionAmbiente
from app.services import audit
from mcp_support import McpDbCase, mcp_actor


class EnvironmentAtomicFlushTestCase(McpDbCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'evidence.sqlite'
        self.app_config = {'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + str(self.database)}
        super().setUp()

    def fingerprint(self):
        count = AuditLog.query.count()
        db.session.remove()
        return hashlib.sha256(self.database.read_bytes()).hexdigest(), count

    async def test_failed_flushed_append_preserves_database_bytes(self):
        self.seed('condiciones_ambiente')
        before = self.fingerprint()
        original = audit.record

        def fail_after_flush(session, actor, action, instance, **kwargs):
            row = original(session, actor, action, instance, **kwargs)
            if isinstance(instance, ComprobacionAmbiente):
                session.flush()
                raise RuntimeError('failure after history flush')
            return row

        with patch.object(audit, 'record', side_effect=fail_after_flush):
            with self.assertLogs('mcp.server.mcpserver.server', level='ERROR'):
                result = await self.call(mcp_actor(), 'qms_create', {
                    'module': 'comprobaciones_ambiente', 'data': {
                        'condicion_id': 1, 'fecha': '2020-01-02',
                        'resultado': 'conforme', 'realizado_por': 'Ana',
                        'evidencia': 'Acta E-2', 'fecha_proxima': '2020-02-01'}})
        self.assertTrue(result.is_error)
        self.assertNotIn('failure after history flush', result.content[0].text)
        self.assertEqual(before, self.fingerprint())
        self.assertEqual(0, ComprobacionAmbiente.query.count())
