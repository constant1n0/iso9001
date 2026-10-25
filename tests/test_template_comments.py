"""Every template closes the HTML comments it opens.

An unclosed ``<!--`` (such as a licence header without its ``-->``) hides the
rest of the page in the browser, while assertions on the rendered HTML still
find the text inside the comment.
"""

from __future__ import annotations

import unittest

import test_auth_bootstrap as bootstrap


TEMPLATES = bootstrap.PROJECT_ROOT / "app" / "templates"


class TemplateCommentsTestCase(unittest.TestCase):
    def test_every_opened_comment_is_closed_before_the_next_one(self) -> None:
        for template in sorted(TEMPLATES.rglob("*.html")):
            source = template.read_text(encoding="utf-8")
            with self.subTest(template=str(template.relative_to(TEMPLATES))):
                position = 0
                while (start := source.find("<!--", position)) != -1:
                    end = source.find("-->", start + 4)
                    self.assertNotEqual(end, -1, f"comment opened at offset {start} is never closed")
                    following = source.find("<!--", start + 4)
                    self.assertTrue(
                        following == -1 or end < following,
                        f"comment opened at offset {start} is still open at offset {following}",
                    )
                    position = end + 3


if __name__ == "__main__":
    unittest.main()
