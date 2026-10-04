"""The UI must advertise the current edition of the standard."""

from __future__ import annotations

import unittest

import test_auth_bootstrap as bootstrap

APP_DIR = bootstrap.PROJECT_ROOT / "app"
TEXT_SUFFIXES = {".py", ".html", ".js", ".css", ".txt", ".md"}


class IsoEditionLabelTestCase(unittest.TestCase):
    def test_no_reference_to_the_2015_edition_remains_in_the_app(self) -> None:
        offenders = []
        for path in APP_DIR.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            if "static/lib" in path.as_posix():
                continue
            if "ISO 9001:2015" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(path.relative_to(APP_DIR).as_posix())
        self.assertEqual([], offenders)

    def test_sidebar_footer_names_the_2026_edition(self) -> None:
        base = (APP_DIR / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("Cláusulas ISO 9001:2026", base)


if __name__ == "__main__":
    unittest.main()
