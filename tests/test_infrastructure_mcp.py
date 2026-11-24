"""MCP infrastructure and immutable-maintenance behavior through the five public tools."""
from app.extensions import db
from app.models import AuditLog
from mcp_support import McpDbCase, mcp_actor, OPERATIVO

MASTER = dict(codigo='INF-1', nombre='Prensa', tipo='equipo_proceso', frecuencia='Cada mes',
              fecha_proxima='2020-01-01', procesos=[])
ENTRY = dict(elemento_id=1, fecha='2020-01-01', tipo='preventivo', resultado='conforme',
             realizado_por='Ana', evidencia='Parte M-1', fecha_proxima='2099-01-01')


class InfrastructureMcpTestCase(McpDbCase):
    async def create(self, slug, data, who=None):
        return await self.call(who or mcp_actor(), 'qms_create', {'module': slug, 'data': data})

    async def state(self):
        result = []
        for module in ('infraestructura', 'mantenimientos_infraestructura'):
            response = await self.call(mcp_actor(), 'qms_list', {'module': module})
            self.assertFalse(response.is_error, response.content)
            result.append(response.structured_content)
        with self.app.app_context():
            from app.services.audit import snapshot
            result.append([snapshot(r) for r in AuditLog.query.order_by(AuditLog.id)])
        return result

    async def test_roundtrip_explicit_date_immutable_history_and_existing_equipment(self):
        created = await self.create('infraestructura', MASTER)
        self.assertFalse(created.is_error, created.content)
        self.assertEqual([], created.structured_content['procesos'])
        self.assertTrue(created.structured_content['vencido'])
        recorded = await self.create('mantenimientos_infraestructura', ENTRY)
        self.assertFalse(recorded.is_error, recorded.content)
        self.assertEqual('Conforme', recorded.structured_content['resultado'])
        state = await self.state()
        self.assertEqual('2099-01-01', state[0]['items'][0]['fecha_proxima'])
        self.assertEqual(['create', 'update', 'create'], [r['action'] for r in state[2]])
        self.assertEqual({7}, {r['actor_user_id'] for r in state[2]})
        self.assertEqual({'mcp'}, {r['channel'] for r in state[2]})
        refused = await self.call(mcp_actor(), 'qms_update', {
            'module': 'mantenimientos_infraestructura', 'id': recorded.structured_content['id'], 'data': {}})
        self.assertTrue(refused.is_error)
        self.assertEqual('Error executing tool qms_update: Los mantenimientos no se modifican ni se eliminan; registra uno nuevo.',
                         refused.content[0].text)
        self.assertEqual(state, await self.state())
        self.assertNotIn('qms_delete', [t.name for t in await self.list_tools()])
        previous = await self.create('equipos_medicion', dict(codigo='EQ-1', nombre='Balanza'))
        self.assertFalse(previous.is_error, previous.content)
        self.assertEqual('sin_comprobar', previous.structured_content['estado'])

    async def test_rejections_are_atomic_permissions_and_optional_nc(self):
        self.assertFalse((await self.create('infraestructura', MASTER)).is_error)
        for changes, message in ((dict(resultado='no_conforme'), 'Un resultado no conforme requiere indicar las acciones realizadas.'),
                                  (dict(fecha='2999-01-01'), 'La fecha de mantenimiento no puede ser futura.'),
                                  (dict(no_conformidad_id=999), 'El campo «no_conformidad_id» no corresponde a ninguna no conformidad.')):
            before = await self.state()
            refused = await self.create('mantenimientos_infraestructura', ENTRY | changes)
            self.assertTrue(refused.is_error)
            self.assertEqual('Error executing tool qms_create: ' + message, refused.content[0].text)
            self.assertEqual(before, await self.state())
        before = await self.state()
        for who in (mcp_actor(OPERATIVO), mcp_actor(scopes=('read',))):
            refused = await self.create('mantenimientos_infraestructura', ENTRY, who)
            self.assertTrue(refused.is_error)
            self.assertIn('No tienes permiso', refused.content[0].text)
        self.assertEqual(before, await self.state())
        nc = await self.create('no_conformidades', dict(descripcion='Fuga', fecha_detectada='2020-01-01'))
        self.assertFalse(nc.is_error, nc.content)
        recorded = await self.create('mantenimientos_infraestructura', ENTRY | dict(
            tipo='correctivo', resultado='no_conforme', acciones='Reparar', no_conformidad_id=nc.structured_content['id']))
        self.assertFalse(recorded.is_error, recorded.content)
        self.assertEqual(nc.structured_content['id'], recorded.structured_content['no_conformidad_id'])
        updated = await self.call(mcp_actor(), 'qms_update', {'module': 'infraestructura', 'id': 1, 'data': {'retirado': True}})
        self.assertFalse(updated.is_error, updated.content)
        before = await self.state()
        refused = await self.create('mantenimientos_infraestructura', ENTRY)
        self.assertTrue(refused.is_error)
        self.assertIn('elemento retirado', refused.content[0].text)
        self.assertEqual(before, await self.state())
