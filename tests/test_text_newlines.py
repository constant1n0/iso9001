"""Browsers post textarea line breaks as CRLF; text is stored with LF.

Without normalisation an unchanged multiline form posted CRLF, compared unequal
to the stored LF text, and wrote a change and an audit row (follow-up
R-crlf-noop found while verifying 8.5.5).
"""

from __future__ import annotations

import unittest

from flask import g

from test_communication_independent import BrowserForm, hashes
from test_production_post_delivery import PostDeliveryCase
from test_nonconformity_service import errors

from app.models import RoleEnum
from app.services import fields


class FieldsTextNewlinesTestCase(unittest.TestCase):
    def test_crlf_and_lone_cr_become_lf_with_or_without_stripping(self) -> None:
        for strip in (True, False):
            with self.subTest(strip=strip):
                self.assertEqual("a\nb\nc", fields.text({"k": "a\r\nb\rc"}, "k", strip=strip))

    def test_length_limit_counts_the_normalised_text(self) -> None:
        self.assertEqual("ab\ncd", fields.text({"k": "ab\r\ncd"}, "k", max_length=5))
        with self.assertRaises(errors().ValidationError):
            fields.text({"k": "ab\r\ncde"}, "k", max_length=5)


class BrowserCrlfRoundTripTestCase(PostDeliveryCase):
    def form(self, path):
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        parser = BrowserForm()
        parser.feed(response.get_data(as_text=True))
        return parser.values

    def test_unchanged_multiline_form_posted_with_crlf_writes_nothing(self) -> None:
        row = self.activity(description="Revisión anual\nSegunda línea", source="Contrato\nAnexo")
        self.login(RoleEnum.ADMINISTRADOR)
        g.pop("_login_user", None)
        path = f"/produccion/posventa/{row.id}/editar"
        form = self.form(path)
        for key in ("description", "source"):
            form[key] = form[key].replace("\r\n", "\n").replace("\n", "\r\n")
        before = hashes()
        self.assertEqual(302, self.client.post(path, data=form).status_code)
        self.assertEqual(before, hashes())


if __name__ == "__main__":
    unittest.main()
