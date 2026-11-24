"""Environment modules use the public tools and keep failed writes atomic."""
from unittest.mock import patch

from app.extensions import db
from app.models import AuditLog
from app.services.audit import snapshot
from mcp_support import McpDbCase, mcp_actor, OPERATIVO

MASTER = dict(proceso_id=1, factor='fisico', descripcion='Temperature', criterio='18–25 °C',
              frecuencia='Monthly', fecha_proxima='2020-01-01')
ENTRY = dict(condicion_id=1, fecha='2020-01-01', resultado='conforme', realizado_por='Ana',
             evidencia='21 °C', fecha_proxima='2099-01-01')


class ProcessEnvironmentMcpTestCase(McpDbCase):
    async def create(self, slug, data, who=None):
        return await self.call(who or mcp_actor(), 'qms_create', {'module': slug, 'data': data})

    async def state(self):
        result = []
        for slug in ('condiciones_ambiente', 'comprobaciones_ambiente'):
            response = await self.call(mcp_actor(), 'qms_list', {'module': slug})
            self.assertFalse(response.is_error, response.content)
            result.append(response.structured_content)
        result.append([snapshot(row) for row in AuditLog.query.order_by(AuditLog.id)])
        return result

    async def prepare(self):
        self.assertFalse((await self.create('procesos', {'proceso': 'Workshop'})).is_error)
        result = await self.create('condiciones_ambiente', MASTER)
        self.assertFalse(result.is_error, result.content)
        return result

    async def test_roundtrip_attribution_immutable_history_and_preserved_registry(self):
        result = await self.prepare()
        self.assertEqual(('Físico', True), (result.structured_content['factor'], result.structured_content['vencida']))
        result = await self.create('comprobaciones_ambiente', ENTRY)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual('Conforme', result.structured_content['resultado'])
        before = await self.state()
        self.assertEqual('2099-01-01', before[0]['items'][0]['fecha_proxima'])
        self.assertEqual(['create', 'create', 'update', 'create'], [r['action'] for r in before[2]])
        self.assertEqual({7}, {r['actor_user_id'] for r in before[2]})
        self.assertEqual({'mcp'}, {r['channel'] for r in before[2]})
        refused = await self.call(mcp_actor(), 'qms_update', {'module': 'comprobaciones_ambiente', 'id': 1, 'data': {}})
        self.assertTrue(refused.is_error)
        self.assertEqual('Error executing tool qms_update: Las comprobaciones no se modifican ni se eliminan; registra una nueva.', refused.content[0].text)
        self.assertEqual(before, await self.state())
        self.assertNotIn('qms_delete', [t.name for t in await self.list_tools()])
        previous = await self.create('infraestructura', dict(codigo='I-1', nombre='Server', tipo='tic',
            frecuencia='Monthly', fecha_proxima='2020-01-01'))
        self.assertFalse(previous.is_error, previous.content)
        self.assertEqual('TIC', previous.structured_content['tipo'])
        listed = await self.call(mcp_actor(), 'qms_list', {'module': 'procesos'})
        self.assertEqual('Workshop', listed.structured_content['items'][0]['proceso'])

    async def test_validation_permissions_retirement_and_no_nc_field(self):
        await self.prepare()
        for changes, message in (({'resultado': 'no_conforme'}, 'Un resultado no conforme requiere indicar las acciones realizadas.'),
                                 ({'fecha': '2999-01-01'}, 'La fecha de comprobación no puede ser futura.'),
                                 ({'fecha_proxima': '2020-01-01'}, 'Indica una nueva próxima fecha posterior a la comprobación.'),
                                 ({'no_conformidad_id': 1}, 'Campos no permitidos: no_conformidad_id.')):
            before = await self.state()
            result = await self.create('comprobaciones_ambiente', ENTRY | changes)
            self.assertTrue(result.is_error)
            self.assertEqual('Error executing tool qms_create: ' + message, result.content[0].text)
            self.assertEqual(before, await self.state())
        before = await self.state()
        for who in (mcp_actor(OPERATIVO), mcp_actor(scopes=('read',))):
            refused = await self.create('comprobaciones_ambiente', ENTRY, who)
            self.assertTrue(refused.is_error)
            self.assertIn('No tienes permiso', refused.content[0].text)
            self.assertEqual(before, await self.state())
        result = await self.create('comprobaciones_ambiente', ENTRY | {'resultado': 'no_conforme', 'acciones': 'Adjust thermostat'})
        self.assertFalse(result.is_error, result.content)
        result = await self.call(mcp_actor(), 'qms_update', {'module': 'condiciones_ambiente', 'id': 1, 'data': {'retirado': True}})
        self.assertFalse(result.is_error, result.content)
        before = await self.state()
        refused = await self.create('comprobaciones_ambiente', ENTRY)
        self.assertTrue(refused.is_error)
        self.assertEqual('Error executing tool qms_create: No se pueden registrar comprobaciones de una condición retirada.', refused.content[0].text)
        self.assertEqual(before, await self.state())

    async def test_append_failure_rolls_back_parent_and_both_audit_rows(self):
        await self.prepare()
        before = await self.state()
        with patch('app.services.process_environment.crud.create', side_effect=RuntimeError('append failed')):
            with self.assertLogs('mcp.server.mcpserver.server', level='ERROR'):
                result = await self.create('comprobaciones_ambiente', ENTRY)
        self.assertTrue(result.is_error)
        self.assertNotIn('append failed', result.content[0].text)
        self.assertEqual(before, await self.state())
