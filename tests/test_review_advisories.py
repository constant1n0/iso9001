"""Public-interface regressions for the Phase 3 review advisories."""
from datetime import date
from app.extensions import db
from app.services import nonconformities, production_changes, production_records
from app.services import production_post_delivery
from app.services.errors import Conflict
from test_communication_independent import BrowserForm, hashes
from test_production_property import PropertyCase, TODAY


class ReviewAdvisories(PropertyCase):
    def test_lot_filter_matches_literal_wildcards_and_escape_character(self) -> None:
        """Literal fragments must not broaden list or paginated results."""
        lots = ('A_1', 'AB1', 'A%1', 'A-long-1', r'A\1', 'A1')
        rows = {lot: self.create(lot=lot).id for lot in lots}
        before = hashes()
        for fragment in ('A_1', 'A%1', r'A\1', 'a_1', ''):
            with self.subTest(fragment=fragment):
                expected = ([rows['A_1']] if fragment == 'a_1' else
                            [rows[fragment]] if fragment else list(rows.values()))
                found = self.service.orders.list_(db.session, self.who, lot=fragment)
                page, total = self.service.orders.list_page(
                    db.session, self.who, lot=fragment)
                self.assertEqual(expected, [row.id for row in found])
                self.assertEqual((expected, len(expected)),
                                 ([row.id for row in page], total))
        self.assertEqual(before, hashes())

    def test_all_cancelled_orders_can_be_replaced_then_authorized(self) -> None:
        """Recovery already permits swapping every cancelled citation, not deletion."""
        from app.services import people
        old = [self.create(), self.create()]
        replacement = self.create(owner_id=1)
        person = people.create(db.session, self.who, {
            'nombre': 'Authority', 'user_id': self.ids[self.who.role]})
        change = production_changes.changes.create(db.session, self.who, dict(
            reason='Recover', description='Replace orders', actions='Recheck',
            review='Checked', order_ids=[row.id for row in old]))
        for row in old:
            self.service.transition(db.session, self.who, row.id, 'cancelled',
                                    cancellation_reason='Stop')
        db.session.commit()
        self.reject(lambda: production_changes.authorize(
            db.session, self.who, change.id, today=TODAY),
            production_changes.INVALID_ORDERS)
        production_changes.changes.update(db.session, self.who, change.id,
                                           {'order_ids': [replacement.id]})
        db.session.commit()
        self.assertEqual([replacement.id], production_changes.changes.get(
            db.session, self.who, change.id).order_ids)
        authority = type(self.who)(person.user_id, 'Authority', self.who.role, 'web')
        production_changes.authorize(db.session, authority, change.id, today=TODAY)
        db.session.commit()
        self.assertEqual('authorized', production_changes.changes.get(
            db.session, self.who, change.id).state.name)
        self.reject(lambda: production_changes.changes.update(
            db.session, self.who, change.id, {}), production_changes.design_changes.IMMUTABLE)
        self.reject(lambda: production_changes.changes.delete(
            db.session, self.who, change.id), production_changes.NO_DELETE)

    def test_duplicate_production_fields_have_actionable_feedback(self) -> None:
        """Refusals explain repetition and preserve submitted input and database."""
        self.create()
        self.login(self.who.role)
        from flask import g
        g.pop('_login_user', None)
        parser = BrowserForm()
        parser.feed(self.client.get('/produccion/1/editar').get_data(as_text=True))
        form = parser.values
        form['title'] = 'Keep my input'
        form.add('code', 'Other code')
        before = hashes()
        response = self.client.post('/produccion/1/editar', data=form)
        self.assertEqual(400, response.status_code)
        page = response.get_data(as_text=True)
        self.assertTrue(
            'Envía cada campo una sola vez y no repitas las opciones seleccionadas.'
            in page, 'Missing actionable repetition feedback')
        self.assertIn('Keep my input', page)
        self.assertEqual(before, hashes())
        form.setlist('code', ['O-1'])
        self.assertEqual(302, self.client.post('/produccion/1/editar', data=form).status_code)
        self.assertEqual('Keep my input', self.service.orders.get(
            db.session, self.who, 1).title)

    def test_release_lock_sql_targets_creation_order_and_decision_release(self) -> None:
        """Local SQL probe names the exact lock that PostgreSQL tests must wait on."""
        from sqlalchemy import event
        from sqlalchemy.dialects import postgresql
        from app.services import people, product_release
        order = self.create(owner_id=1)
        self.service.transition(db.session, self.who, order.id, 'in_progress')
        self.service.transition(db.session, self.who, order.id, 'finished')
        person = people.create(db.session, self.who, {
            'nombre': 'Authority', 'user_id': self.ids[self.who.role]})
        db.session.commit()
        who = type(self.who)(person.user_id, 'Authority', self.who.role, 'web')
        seen = []
        def capture(state):
            if state.is_select and state.statement._for_update_arg is not None:
                compiled = state.statement.compile(dialect=postgresql.dialect())
                seen.append((str(compiled), list(compiled.params.values()),
                             state.execution_options.get('populate_existing')))
        event.listen(db.session(), 'do_orm_execute', capture)
        try:
            release = product_release.releases.create(
                db.session, who, {'order_id': order.id})
            db.session.commit()
            sql, params, refresh = seen[0]
            self.assertIn('FROM production_orders', sql)
            self.assertIn('FOR UPDATE', sql)
            self.assertEqual([order.id], params)
            self.assertTrue(refresh)
            seen.clear()
            product_release.reject(db.session, who, release.id,
                                   reason='Not suitable', today=TODAY)
            db.session.commit()
            for entry, table, record_id in zip(seen[:2],
                    ('product_releases', 'production_orders'), (release.id, order.id)):
                sql, params, refresh = entry
                self.assertIn('FROM ' + table, sql)
                self.assertIn('FOR UPDATE', sql)
                self.assertEqual([record_id], params)
                self.assertTrue(refresh)
            self.assertGreaterEqual(len(seen), 2)
        finally:
            event.remove(db.session(), 'do_orm_execute', capture)

    def test_other_restrict_citations_also_prevent_nc_delete(self) -> None:
        """Complaints, knowledge and improvements retain their exact NC citation."""
        from app.services import reclamaciones, knowledge, improvements
        for kind in ('complaint', 'knowledge', 'improvement'):
            with self.subTest(kind=kind):
                nc = nonconformities.create(db.session, self.who, {
                    'descripcion': kind, 'fecha_detectada': TODAY})
                if kind == 'complaint':
                    reclamaciones.create(db.session, self.who, dict(
                        cliente_id=1, descripcion='Complaint', no_conformidad_id=nc.id),
                        today=TODAY)
                elif kind == 'knowledge':
                    knowledge.items.create(db.session, self.who, dict(
                        kind='leccion', title='Lesson', summary='Evidence', owner_id=1,
                        no_conformidad_id=nc.id))
                else:
                    improvements.create(db.session, self.who, dict(
                        descripcion='Improve', fecha_identificacion=TODAY,
                        origen='no_conformidad', no_conformidad_id=nc.id), today=TODAY)
                db.session.commit()
                self.reject(lambda: nonconformities.delete(db.session, self.who, nc.id),
                    Conflict.default_message, Conflict)

    def test_production_citations_prevent_nc_delete_without_sqlite_foreign_keys(self) -> None:
        """Every production citation refuses deletion before any audit mutation."""
        self.create()
        self.item()
        self.service.transition(db.session, self.who, 1, 'in_progress')
        db.session.commit()
        self.assertEqual(0, db.session.scalar(db.text('PRAGMA foreign_keys')))
        for kind in ('control', 'preservation', 'incident', 'post_delivery'):
            nc = nonconformities.create(db.session, self.who, {
                'descripcion': kind, 'fecha_detectada': date(2020, 1, 1)})
            values = dict(order_id=1, date=TODAY, responsible_id=1,
                          description='Evidence', result='conforming',
                          nonconformity_id=nc.id)
            if kind == 'control':
                register = production_records.controls
                cited = register.create(db.session, self.who,
                    values | {'kind': 'inspection', 'evidence': 'Report'}, today=TODAY)
            elif kind == 'preservation':
                register = production_records.preservation
                cited = register.create(db.session, self.who,
                    values | {'aspect': 'storage'}, today=TODAY)
            elif kind == 'incident':
                register = self.property.incidents
                cited = self.incident(nonconformity_id=nc.id)
            else:
                register = production_post_delivery.activities
                cited = register.create(db.session, self.who, dict(
                    description='Warranty', kind='warranty', responsible_id=1,
                    due_date=TODAY, nonconformity_id=nc.id), today=TODAY)
            db.session.commit()
            self.reject(lambda: nonconformities.delete(db.session, self.who, nc.id),
                Conflict.default_message, Conflict)
            self.assertEqual(nc.id, register.get(db.session, self.who, cited.id).nonconformity_id)
            self.assertEqual(nc.id, nonconformities.get(db.session, self.who, nc.id).id)
        unused = nonconformities.create(db.session, self.who, {
            'descripcion': 'Unused', 'fecha_detectada': TODAY})
        db.session.commit()
        nonconformities.delete(db.session, self.who, unused.id)
        db.session.commit()
        self.assertNotIn(unused.id, [row.id for row in nonconformities.list_(
            db.session, self.who)])
