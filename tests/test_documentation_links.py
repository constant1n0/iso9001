"""Documentation: every relative Markdown link and #anchor resolves."""

from __future__ import annotations

import re
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[(?:[^\]\[]|\[[^\]]*\])*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"(```+|~~~+)")


def documents(root: Path) -> list[Path]:
    """The README files and every Markdown file under ``docs/``."""
    return [*sorted(root.glob("README*.md")), *sorted((root / "docs").rglob("*.md"))]


def outside_code(text: str) -> list[tuple[int, str]]:
    """Numbered lines outside fenced blocks, with inline code removed."""
    lines, fence = [], None
    for number, line in enumerate(text.splitlines(), 1):
        marker = FENCE.match(line.lstrip())
        if marker:
            if fence is None:
                fence = marker.group(1)[0] * 3
            elif line.lstrip().startswith(fence):
                fence = None
            continue
        if fence is None:
            lines.append((number, re.sub(r"`[^`]*`", "", line)))
    return lines


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, accents kept, spaces to hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).replace("`", "").replace("*", "")
    text = unicodedata.normalize("NFC", text).strip().lower()
    return "".join(c for c in text if c.isalnum() or c in " -_").replace(" ", "-")


def anchors(path: Path) -> set[str]:
    """Every heading anchor of ``path``, numbering repeated headings as GitHub does."""
    seen: dict[str, int] = {}
    for _, line in outside_code(path.read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if match:
            base = slug(match.group(2))
            seen[base] = seen.get(base, 0) + 1
    return {f"{base}-{n}" if n else base
            for base, count in seen.items() for n in range(count)}


def broken_links(root: Path) -> list[str]:
    """``file:line: target`` for each relative link whose file or anchor is missing."""
    broken = []
    for document in documents(root):
        for number, line in outside_code(document.read_text(encoding="utf-8")):
            for target in LINK.findall(line):
                if re.match(r"[a-z][a-z0-9+.-]*:", target):
                    continue  # http(s), mailto and other absolute links
                path, _, anchor = target.partition("#")
                destination = (document.parent / path).resolve() if path else document
                where = f"{document.relative_to(root)}:{number}: {target}"
                if not destination.exists():
                    broken.append(f"{where} (missing file)")
                elif anchor and destination.suffix == ".md" and anchor not in anchors(destination):
                    broken.append(f"{where} (missing anchor)")
    return broken


class DocumentationLinksTestCase(unittest.TestCase):
    def test_every_relative_link_and_anchor_resolves(self) -> None:
        self.assertGreater(len(documents(ROOT)), 5)
        self.assertEqual([], broken_links(ROOT))

    def test_a_missing_file_or_anchor_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "guía.md").write_text("# Guía\n\n## Términos de uso\n",
                                                   encoding="utf-8")
            (root / "README.md").write_text(
                "[ok](docs/guía.md#términos-de-uso) [web](https://example.org)\n"
                "`[code](nowhere.md)` [file](docs/none.md) [anchor](docs/guía.md#nada)\n",
                encoding="utf-8")
            self.assertEqual(["README.md:2: docs/none.md (missing file)",
                              "README.md:2: docs/guía.md#nada (missing anchor)"],
                             broken_links(root))


if __name__ == "__main__":
    unittest.main()
