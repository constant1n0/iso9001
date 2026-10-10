# Este archivo es parte de "ISO9001 QMS".
#
# "ISO9001 QMS" es software libre: puede redistribuirlo y/o modificarlo
# bajo los términos de la Licencia Pública General GNU publicada por la
# Free Software Foundation, ya sea la versión 3 de la Licencia o (a su
# elección) cualquier versión posterior.
#
# "ISO9001 QMS" se distribuye con la esperanza de que sea útil,
# pero SIN NINGUNA GARANTÍA; incluso sin la garantía implícita de
# COMERCIABILIDAD o IDONEIDAD PARA UN PROPÓSITO PARTICULAR. Consulte la
# Licencia Pública General GNU para obtener más detalles.
#
# Debería haber recibido una copia de la Licencia Pública General GNU
# junto con este programa. En caso contrario, consulte <https://www.gnu.org/licenses/>.

"""The framework-free storage of revision attachments (decision DC7).

Files are checked by content (magic bytes) and extension, written atomically
under a random name, and read back or removed only by that name.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import test_auth_bootstrap  # noqa: F401  (isolated import of the app package)

from app.services import document_files as files
from app.services.errors import ValidationError

MAX = 1_000_000
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
ODT_MIME = b"application/vnd.oasis.opendocument.text"
OOXML_TYPES = b'<?xml version="1.0"?><Types xmlns="x"/>'


def zipped(entries: list[tuple[str, bytes]], *, first_stored: bool = False) -> bytes:
    """A ZIP archive of ``entries`` (name, content), in order."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (name, content) in enumerate(entries):
            method = zipfile.ZIP_STORED if first_stored and index == 0 else zipfile.ZIP_DEFLATED
            archive.writestr(zipfile.ZipInfo(name), content, compress_type=method)
    return buffer.getvalue()


def docx() -> bytes:
    return zipped([("[Content_Types].xml", OOXML_TYPES), ("_rels/.rels", b"<r/>"),
                   ("word/document.xml", b"<w:document/>")])


def xlsx() -> bytes:
    return zipped([("[Content_Types].xml", OOXML_TYPES), ("xl/workbook.xml", b"<workbook/>")])


def odt(mimetype: bytes = ODT_MIME) -> bytes:
    return zipped([("mimetype", mimetype), ("content.xml", b"<office:document-content/>")],
                  first_stored=True)


SAMPLES = {
    "pdf": (PDF, "application/pdf"),
    "docx": (docx(), "application/vnd.openxmlformats-officedocument."
                     "wordprocessingml.document"),
    "xlsx": (xlsx(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "odt": (odt(), "application/vnd.oasis.opendocument.text"),
}


class StorageBase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = tempfile.mkdtemp(prefix="iso9001-files-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.base, ignore_errors=True))

    def store(self, content: bytes, name: str, max_bytes: int = MAX):
        return files.store(self.base, io.BytesIO(content), name, max_bytes=max_bytes)

    def refused(self, content: bytes, name: str, max_bytes: int = MAX) -> str:
        """Assert the upload is refused, leaves the directory empty and return why."""
        with self.assertRaises(ValidationError) as caught:
            self.store(content, name, max_bytes)
        self.assertEqual([], os.listdir(self.base), "no file or temporary file may remain")
        return caught.exception.message


class AcceptedTypesTestCase(StorageBase):
    def test_each_accepted_type_is_stored_under_a_random_name_with_its_digest(self) -> None:
        for extension, (content, mime) in SAMPLES.items():
            with self.subTest(extension=extension):
                stored = self.store(content, f"Informe final.{extension.upper()}")
                self.assertRegex(stored.stored_name, r"^[0-9a-f]{32}$")
                self.assertEqual(f"Informe final.{extension.upper()}", stored.display_name)
                self.assertEqual((len(content), hashlib.sha256(content).hexdigest(), mime),
                                 (stored.size, stored.sha256, stored.mime))
                with open(os.path.join(self.base, stored.stored_name), "rb") as handle:
                    self.assertEqual(content, handle.read())
        names = os.listdir(self.base)
        self.assertEqual(4, len(set(names)))
        self.assertFalse(any("Informe" in name for name in names))

    def test_stored_files_are_private_to_the_server_account(self) -> None:
        stored = self.store(PDF, "a.pdf")
        mode = os.stat(os.path.join(self.base, stored.stored_name)).st_mode & 0o777
        self.assertEqual(0o600, mode)

    def test_a_large_file_is_read_in_chunks_up_to_the_limit(self) -> None:
        content = PDF + b"0" * 300_000
        stored = self.store(content, "grande.pdf", max_bytes=len(content))
        self.assertEqual((len(content), hashlib.sha256(content).hexdigest()),
                         (stored.size, stored.sha256))


class RefusedContentTestCase(StorageBase):
    def test_the_extension_must_match_the_detected_type(self) -> None:
        for content, name in ((PDF, "a.docx"), (docx(), "a.xlsx"), (xlsx(), "a.odt"),
                              (odt(), "a.pdf"), (docx(), "a.pdf")):
            with self.subTest(name=name):
                self.assertEqual(files.EXTENSION_MISMATCH, self.refused(content, name))

    def test_unsupported_extensions_are_refused_before_reading(self) -> None:
        for name in ("a.exe", "a.html", "a.pdf.exe", "a", "", None, "a.doc", "a.ods"):
            with self.subTest(name=name):
                self.assertEqual(files.UNSUPPORTED, self.refused(PDF, name))

    def test_renamed_executables_html_and_other_content_are_refused(self) -> None:
        samples = {
            "executable": b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64,
            "elf": b"\x7fELF\x02\x01\x01" + b"\x00" * 64,
            "html": b"<!DOCTYPE html><html><script>alert(1)</script></html>",
            "svg": b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            "pdf-later": b"   %PDF-1.7 not at the start",
            "zip-without-office-parts": zipped([("readme.txt", b"hola")]),
            "ooxml-without-word": zipped([("[Content_Types].xml", OOXML_TYPES)]),
            "ooxml-word-and-xl": zipped([("[Content_Types].xml", OOXML_TYPES),
                                         ("word/a.xml", b"a"), ("xl/b.xml", b"b")]),
            "spreadsheet-odf": odt(b"application/vnd.oasis.opendocument.spreadsheet"),
            "odt-mimetype-not-first": zipped([("content.xml", b"<c/>"), ("mimetype", ODT_MIME)]),
            "html-with-zip-appended": b"<html></html>" + docx(),
            "truncated-zip": docx()[:40],
        }
        for label, content in samples.items():
            for extension in ("pdf", "docx", "xlsx", "odt"):
                with self.subTest(label=label, extension=extension):
                    self.assertIn(self.refused(content, f"x.{extension}"),
                                  (files.UNRECOGNIZED, files.EXTENSION_MISMATCH))
        self.assertEqual(files.UNRECOGNIZED, self.refused(samples["executable"], "x.pdf"))
        self.assertEqual(files.UNRECOGNIZED, self.refused(samples["html"], "x.docx"))

    def test_an_empty_file_is_refused(self) -> None:
        self.assertEqual(files.EMPTY, self.refused(b"", "vacio.pdf"))

    def test_an_oversized_file_is_refused_while_streaming(self) -> None:
        class Endless(io.RawIOBase):
            """A stream that never ends: only the limit stops the copy."""

            def __init__(self) -> None:
                self.served = 0

            def readable(self) -> bool:
                return True

            def readinto(self, buffer) -> int:
                buffer[:] = b"%" * len(buffer)
                self.served += len(buffer)
                return len(buffer)

        endless = Endless()
        with self.assertRaises(ValidationError) as caught:
            files.store(self.base, endless, "a.pdf", max_bytes=100_000)
        self.assertEqual(files.too_large(100_000), caught.exception.message)
        self.assertLess(endless.served, 100_000 + 2 * files.CHUNK_SIZE)
        self.assertEqual([], os.listdir(self.base))
        self.assertIn("MB", files.too_large(20 * 1024 * 1024))

    def test_a_file_one_byte_over_the_limit_is_refused(self) -> None:
        self.assertEqual(files.too_large(len(PDF) - 1), self.refused(PDF, "a.pdf", len(PDF) - 1))
        self.assertEqual(len(PDF), self.store(PDF, "a.pdf", len(PDF)).size)


class ZipBombTestCase(StorageBase):
    def test_too_many_entries_are_refused_without_reading_them(self) -> None:
        crowded = zipped([("[Content_Types].xml", OOXML_TYPES)]
                         + [(f"word/p{n}.xml", b"x") for n in range(30)])
        with patch.object(files, "MAX_ZIP_ENTRIES", 10):
            self.assertEqual(files.UNRECOGNIZED, self.refused(crowded, "a.docx"))
        self.assertTrue(self.store(crowded, "a.docx").stored_name)

    def test_a_huge_declared_uncompressed_size_is_refused(self) -> None:
        bomb = zipped([("[Content_Types].xml", OOXML_TYPES),
                       ("word/document.xml", b"\x00" * 3_000_000)])
        self.assertLess(len(bomb), 50_000)
        with patch.object(files, "MAX_ZIP_UNCOMPRESSED", 1_000_000):
            self.assertEqual(files.UNRECOGNIZED, self.refused(bomb, "a.docx"))

    def test_an_oversized_central_directory_is_refused_before_parsing(self) -> None:
        crowded = zipped([("[Content_Types].xml", OOXML_TYPES)]
                         + [(f"word/{'p' * 50}{n}.xml", b"x") for n in range(40)])
        with patch.object(files, "MAX_ZIP_DIRECTORY", 1_000), \
                patch.object(files.zipfile, "ZipFile") as parser:
            self.assertEqual(files.UNRECOGNIZED, self.refused(crowded, "a.docx"))
        parser.assert_not_called()

    def test_zip64_markers_are_refused_whatever_the_limits(self) -> None:
        end = docx().rfind(b"PK\x05\x06")
        for start, marker in ((8, b"\xff\xff"), (10, b"\xff\xff"), (12, b"\xff" * 4)):
            marked = bytearray(docx())
            marked[end + start:end + start + len(marker)] = marker
            with self.subTest(offset=start), \
                    patch.object(files, "MAX_ZIP_ENTRIES", 0x10000), \
                    patch.object(files, "MAX_ZIP_DIRECTORY", 0x100000000), \
                    patch.object(files.zipfile, "ZipFile") as parser:
                self.assertEqual(files.UNRECOGNIZED, self.refused(bytes(marked), "a.docx"))
                parser.assert_not_called()

    def test_an_odt_mimetype_with_trailing_data_is_refused(self) -> None:
        sneaky = odt(ODT_MIME + b"\x00" * 100_000)
        self.assertEqual(files.UNRECOGNIZED, self.refused(sneaky, "a.odt"))


class AtomicWriteTestCase(StorageBase):
    def test_a_failure_while_writing_leaves_no_temporary_file(self) -> None:
        class Broken(io.RawIOBase):
            def readable(self) -> bool:
                return True

            def readinto(self, buffer) -> int:
                raise OSError("connection reset")

        with self.assertRaises(OSError):
            files.store(self.base, Broken(), "a.pdf", max_bytes=MAX)
        self.assertEqual([], os.listdir(self.base))

    def test_a_failure_while_moving_into_place_leaves_no_temporary_file(self) -> None:
        with patch.object(files.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store(PDF, "a.pdf")
        self.assertEqual([], os.listdir(self.base))

    def test_the_temporary_file_lives_in_the_storage_directory(self) -> None:
        seen = []
        real_replace = os.replace

        def spy(source, target):
            seen.append((os.path.dirname(source), os.path.dirname(target)))
            return real_replace(source, target)

        with patch.object(files.os, "replace", side_effect=spy):
            self.store(PDF, "a.pdf")
        self.assertEqual([(self.base, self.base)], seen)


class DisplayNameTestCase(unittest.TestCase):
    def test_path_components_and_control_characters_are_removed(self) -> None:
        cases = {
            "../../etc/informe.pdf": "informe.pdf",
            "C:\\Users\\ana\\Plan de calidad.docx": "Plan de calidad.docx",
            "a\x00b\r\nc\u202egnp.pdf": "abcgnp.pdf",
            "  .oculto.pdf  ": "oculto.pdf",
            "Ñandú técnico.odt": "Ñandú técnico.odt",
            "": "",
            None: "",
            "../..": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, files.display_name(raw))

    def test_long_names_are_capped_and_keep_their_extension(self) -> None:
        name = files.display_name("x" * 400 + ".xlsx")
        self.assertEqual(files.NAME_MAX, len(name))
        self.assertTrue(name.endswith("x.xlsx"))

    def test_sizes_read_in_bytes_kilobytes_or_megabytes_with_a_decimal_comma(self) -> None:
        for size, shown in ((0, "0 B"), (1023, "1023 B"), (1024, "1 KB"), (1536, "1,5 KB"),
                            (200_000, "195,3 KB"), (1_572_864, "1,5 MB"),
                            (20 * 1024 * 1024, "20 MB")):
            with self.subTest(size=size):
                self.assertEqual(shown, files.human_size(size))
        self.assertEqual("El fichero supera el tamaño máximo de 20 MB.",
                         files.too_large(20 * 1024 * 1024))

    def test_the_content_disposition_is_an_rfc_5987_attachment(self) -> None:
        self.assertEqual(
            "attachment; filename=\"Plan tecnico_v2_.pdf\"; "
            "filename*=UTF-8''Plan%20t%C3%A9cnico%22v2%22.pdf",
            files.content_disposition('Plan técnico"v2".pdf'))
        self.assertEqual("attachment; filename=\"documento\"; filename*=UTF-8''%E2%82%AC",
                         files.content_disposition("€"))


class OpenAndRemoveTestCase(StorageBase):
    def test_a_stored_file_is_opened_and_removed_by_its_name(self) -> None:
        stored = self.store(PDF, "a.pdf")
        with files.open_stored(self.base, stored.stored_name) as handle:
            self.assertEqual(PDF, handle.read())
        self.assertTrue(files.remove(self.base, stored.stored_name))
        self.assertFalse(files.remove(self.base, stored.stored_name))
        with self.assertRaises(FileNotFoundError):
            files.open_stored(self.base, stored.stored_name)

    def test_names_other_than_a_plain_uuid_are_refused(self) -> None:
        outside = os.path.join(os.path.dirname(self.base), "secret.txt")
        bad = ("../secret.txt", outside, "..", ".", "", None, 7, "a" * 32 + "/",
               "A" * 32, "0" * 31, "0" * 33, "0" * 32 + ".pdf", "0" * 32 + "\x00",
               "../" + "0" * 32, "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4
               + "-" + "0" * 12)
        for name in bad:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    files.open_stored(self.base, name)
                with self.assertRaises(ValueError):
                    files.remove(self.base, name)

    def test_a_symbolic_link_or_directory_is_never_opened(self) -> None:
        target = tempfile.NamedTemporaryFile(delete=False)
        self.addCleanup(os.unlink, target.name)
        link, folder = "1" * 32, "2" * 32
        os.symlink(target.name, os.path.join(self.base, link))
        os.mkdir(os.path.join(self.base, folder))
        for name in (link, folder):
            with self.subTest(name=name):
                with self.assertRaises(FileNotFoundError):
                    files.open_stored(self.base, name)

    def test_stored_names_lists_only_plain_uuid_files(self) -> None:
        stored = self.store(PDF, "a.pdf")
        open(os.path.join(self.base, ".upload-x.tmp"), "wb").close()
        open(os.path.join(self.base, "notes.txt"), "wb").close()
        os.mkdir(os.path.join(self.base, "3" * 32))
        listed = files.stored_names(self.base)
        self.assertEqual([stored.stored_name], [entry.name for entry in listed.stored])
        self.assertEqual(3, listed.ignored)


if __name__ == "__main__":
    unittest.main()
