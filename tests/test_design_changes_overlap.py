"""Native review R3-001: overlapping authorizations must not strand a project."""
import unittest

import test_design_changes as fixtures
from test_communication_independent import hashes
from test_design import TODAY
from app.extensions import db
from app.services import design, design_changes as changes


class OverlappingAuthorizationTests(fixtures.ChangeCase):
    def test_second_authorization_waits_and_the_project_can_still_complete(self):
        self.stage(self.project)
        for target in ('planificado', 'en_curso'):
            design.transition(db.session, self.who, self.project.id, target)
        db.session.commit()
        output = self.approve(self.output())
        first = self.authorize(self.propose(output_ids=[output.id]))
        second = self.propose(output_ids=[output.id])
        before = hashes()
        self.reject(lambda: changes.authorize(db.session, self.admin, second.id, today=TODAY),
                    changes.OVERLAPPING)
        self.assertEqual(before, hashes())
        new_version = changes.apply(db.session, self.admin, first.id)[0]
        db.session.commit()
        self.approve(new_version)
        # The waiting proposal now names a superseded output: it can be rejected,
        # never authorized against stale content.
        self.reject(lambda: changes.authorize(db.session, self.admin, second.id, today=TODAY),
                    changes.INVALID_OUTPUTS)
        changes.reject(db.session, self.admin, second.id, reason='Superseded by the first change')
        db.session.commit()
        design.complete(db.session, self.admin, self.project.id, today=TODAY)
        db.session.commit()
        self.assertEqual('completado', self.project.state.name)


if __name__ == '__main__':
    unittest.main()
