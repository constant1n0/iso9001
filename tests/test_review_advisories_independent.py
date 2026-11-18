"""Independent S1/A4/A8 scope and integrity regressions."""
from unittest.mock import patch
from flask import g
from app.extensions import db
from app.services import (audit, nonconformities as nc, corrective_actions,
    reclamaciones, knowledge, improvements, production_records as records,
    production_post_delivery as post, qms_changes)
from app.services.errors import Conflict
from test_production_property import PropertyCase, TODAY
from test_communication_independent import BrowserForm, hashes


class IndependentAdvisories(PropertyCase):
    def browser(self, path):
        parser = BrowserForm()
        parser.feed(self.client.get(path).get_data(as_text=True))
        return parser.values

    def test_a4_other_routes_keep_existing_duplicate_feedback(self):
        self.create()
        self.item()
        self.login(self.who.role)
        g.pop('_login_user', None)
        for path in ('/produccion/propiedad/1/editar', '/produccion/posventa/nueva',
                     '/produccion/cambios/nuevo', '/produccion/1/preservacion/nuevo'):
            with self.subTest(path=path):
                form = self.browser(path)
                key = next(k for k in form if k != 'csrf_token')
                form.add(key, form[key])
                before = hashes()
                response = self.client.post(path, data=form)
                self.assertEqual(400, response.status_code)
                self.assertIn('No repitas los campos del control.',
                              response.get_data(as_text=True))
                self.assertNotIn('Envía cada campo una sola vez',
                                 response.get_data(as_text=True))
                self.assertEqual(before, hashes())

    def test_a4_unrelated_validation_keeps_existing_form_help(self):
        self.create()
        self.login(self.who.role)
        g.pop('_login_user', None)
        path = '/produccion/1/editar'
        form = self.browser(path)
        form['title'] = ''
        before = hashes()
        response = self.client.post(path, data=form)
        self.assertEqual(200, response.status_code)
        self.assertIn('Mantén Ctrl (o Cmd) para seleccionar varios elementos.</p>',
                      response.get_data(as_text=True))
        self.assertEqual(before, hashes())

    def test_a8_all_restrict_citations_refuse_before_any_write(self):
        self.create()
        self.item()
        self.service.transition(db.session, self.who, 1, 'in_progress')
        db.session.commit()
        refs = {fk.parent for table in db.metadata.tables.values()
                for fk in table.foreign_keys if fk.column.table.name == 'no_conformidades'
                and fk.ondelete == 'RESTRICT'}
        self.assertEqual(refs, {c.property.columns[0] for c in nc.REFERENCES}
                         | {db.metadata.tables['cambios_sgc'].c.no_conformidad_id})
        for enabled in (0, 1):
            db.session.commit()
            db.session.execute(db.text(f'PRAGMA foreign_keys={enabled}'))
            self.assertEqual(enabled, db.session.scalar(db.text('PRAGMA foreign_keys')))
            for kind in ('complaint', 'knowledge', 'improvement', 'control',
                         'preservation', 'incident', 'post', 'qms'):
                with self.subTest(foreign_keys=enabled, kind=kind):
                    row = nc.create(db.session, self.who, dict(descripcion=kind, fecha_detectada=TODAY))
                    link = dict(no_conformidad_id=row.id)
                    values = dict(nonconformity_id=row.id)
                    if kind == 'complaint': reclamaciones.create(db.session, self.who, dict(cliente_id=1, descripcion='C', **link), today=TODAY)
                    if kind == 'knowledge': knowledge.items.create(db.session, self.who, dict(kind='leccion', title='K', summary='S', owner_id=1, **link))
                    if kind == 'improvement': improvements.create(db.session, self.who, dict(descripcion='I', fecha_identificacion=TODAY, origen='no_conformidad', **link), today=TODAY)
                    if kind in ('control', 'preservation'):
                        register = records.controls if kind == 'control' else records.preservation
                        register.create(db.session, self.who, dict(order_id=1, date=TODAY, responsible_id=1, description='R', result='conforming', **values, **({'kind': 'inspection', 'evidence': 'E'} if kind == 'control' else {'aspect': 'storage'})), today=TODAY)
                    if kind == 'incident': self.incident(**values)
                    if kind == 'post': post.activities.create(db.session, self.who, dict(description='P', kind='warranty', responsible_id=1, due_date=TODAY, **values), today=TODAY)
                    if kind == 'qms': qms_changes.changes.create(db.session, self.who, dict(titulo='Q', descripcion='Q', origen='no_conformidad', proposito='Q', responsable_id=1, fecha_propuesta=TODAY, **link), today=TODAY)
                    corrective_actions.create(db.session, self.who, dict(descripcion='Fix', responsable_id=1, fecha_prevista=TODAY, **link))
                    db.session.commit()
                    before = hashes()
                    with patch.object(audit, 'record', wraps=audit.record) as record:
                        with self.assertRaises(Conflict) as caught:
                            nc.delete(db.session, self.who, row.id)
                        record.assert_not_called()
                    self.assertEqual(nc.CITED_BY_CHANGES if kind == 'qms' else Conflict.default_message, str(caught.exception))
                    self.assertEqual(before, hashes())
                    db.session.rollback()
                    self.assertEqual(before, hashes())
