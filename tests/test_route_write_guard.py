"""Routes are thin adapters: they never write audited models through the session.

Every write goes through ``app.services`` so it is authorized, validated,
attributed and audited. Routes may still ``commit`` and ``rollback`` because
the adapter owns the transaction.
"""

from __future__ import annotations

import ast
import pathlib
import tempfile
import unittest

import app.routes as routes_pkg
from app.services.audit import AUDITED_MODELS

AUDITED_NAMES = frozenset(model.__name__ for model in AUDITED_MODELS)
SESSION_WRITES = frozenset({"add", "add_all", "delete", "merge", "bulk_save_objects",
                            "bulk_insert_mappings", "bulk_update_mappings"})
QUERY_WRITES = frozenset({"delete", "update"})  # Model.query.filter(...).delete() skips the ORM


def _chain(node: ast.AST) -> list[str]:
    """Attribute and name parts of a call chain, outermost last (``a.b().c`` -> a, b, c)."""
    parts: list[str] = []
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Call):
            node = node.func
        else:
            node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return parts[::-1]


def write_violations(source: str, audited_names: frozenset[str] = AUDITED_NAMES) -> list[str]:
    """Direct session writes, bulk query writes and audited-model instantiations in ``source``."""
    tree = ast.parse(source)
    names = set(audited_names)
    for node in ast.walk(tree):  # follow ``from ..models import X as Y`` aliases
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname for a in node.names if a.asname and a.name in audited_names}
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts = _chain(node.func)
        if len(parts) >= 2 and parts[-1] in SESSION_WRITES and parts[-2] == "session":
            found.append(f"line {node.lineno}: session.{parts[-1]}")
        elif len(parts) >= 3 and parts[-1] in QUERY_WRITES and "query" in parts[:-1]:
            found.append(f"line {node.lineno}: query.{parts[-1]}")
        elif parts and parts[-1] in names:
            found.append(f"line {node.lineno}: instantiates {parts[-1]}")
    return found


def route_violations(directory: pathlib.Path) -> dict[str, list[str]]:
    """Violations per module of ``directory`` (empty when every route is a clean adapter)."""
    result = {}
    for path in sorted(directory.glob("*.py")):
        found = write_violations(path.read_text())
        if found:
            result[path.name] = found
    return result


BAD_SAMPLE = '''
from ..extensions import db
from ..models import NoConformidad as NC, Mejora

def create():
    db.session.add(NC(descripcion="x"))
    db.session.add_all([Mejora(no_conformidad="y")])

def remove(row):
    db.session.delete(row)
    NC.query.filter_by(id=1).delete()
    db.session.merge(row)
'''

GOOD_SAMPLE = '''
from ..extensions import db
from ..models import User

def view():
    db.session.commit()
    db.session.rollback()
    return User.query.count(), db.session.query(User).all(), {}.update({})
'''


class RouteWriteGuardTestCase(unittest.TestCase):
    def test_the_guard_flags_every_kind_of_direct_write(self) -> None:
        found = write_violations(BAD_SAMPLE)
        for expected in ("session.add", "session.add_all", "session.delete", "session.merge",
                         "query.delete", "instantiates NC", "instantiates Mejora"):
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in item for item in found), found)

    def test_the_guard_allows_commit_rollback_and_reads(self) -> None:
        self.assertEqual([], write_violations(GOOD_SAMPLE))

    def test_a_bad_module_in_a_directory_is_reported_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "bad_routes.py").write_text(BAD_SAMPLE)
            (root / "good_routes.py").write_text(GOOD_SAMPLE)
            self.assertEqual(["bad_routes.py"], list(route_violations(root)))

    def test_the_audited_models_are_known(self) -> None:
        self.assertIn("NoConformidad", AUDITED_NAMES)
        self.assertNotIn("User", AUDITED_NAMES)  # credentials are never audited

    def test_no_route_writes_audited_models_directly(self) -> None:
        root = pathlib.Path(routes_pkg.__file__).parent
        self.assertGreater(len(list(root.glob("*_routes.py"))), 5)
        self.assertEqual({}, route_violations(root))


if __name__ == "__main__":
    unittest.main()
