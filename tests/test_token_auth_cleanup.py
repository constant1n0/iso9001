"""The unused JWT stack stays gone; token authentication is documented."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REMOVED = ("flask_jwt_extended", "jwt")


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.add(node.module.split(".")[0])
    return modules


class DependencyCleanupTestCase(unittest.TestCase):
    def test_requirements_do_not_list_flask_jwt_extended(self) -> None:
        # PyJWT stays in the freeze only as a transitive dependency of ``mcp``.
        names = {
            line.split("==")[0].strip().lower().replace("_", "-")
            for line in (ROOT / "requirements.txt").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        }
        self.assertNotIn("flask-jwt-extended", names)

    def test_nothing_imports_the_jwt_packages(self) -> None:
        sources = [
            *ROOT.glob("*.py"), *(ROOT / "app").rglob("*.py"),
            *(ROOT / "tests").glob("*.py"), *(ROOT / "migrations").rglob("*.py"),
        ]
        offenders = [str(p.relative_to(ROOT)) for p in sources
                     if imported_modules(p) & set(REMOVED)]
        self.assertEqual([], offenders)


class DocumentationTestCase(unittest.TestCase):
    def test_readme_documents_the_token_commands(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in ("create-api-token", "list-api-tokens", "revoke-api-token"):
            self.assertIn(command, readme)

    def test_service_doc_explains_how_an_adapter_authenticates(self) -> None:
        doc = (ROOT / "docs" / "architecture" / "services.md").read_text(encoding="utf-8")
        for expected in ("api_tokens.authenticate", "AuthenticationFailed", "SECRET_KEY",
                         "expires", "scopes"):
            self.assertIn(expected, doc)


if __name__ == "__main__":
    unittest.main()
