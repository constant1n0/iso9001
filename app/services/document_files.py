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

"""Storage of revision attachments on the server's disk (decision DC7).

Framework-free: every function receives the storage directory. ``store``
copies an uploaded stream in chunks into a temporary file of that directory,
enforcing the size limit as it reads, and moves it into place under a random
name (``uuid4().hex``) only once its content is recognized: a PDF (``%PDF-``),
a DOCX or XLSX (a ZIP with ``[Content_Types].xml`` and ``word/`` or ``xl/``)
or an ODT (a ZIP whose first entry ``mimetype`` names an OpenDocument text).
The file's extension must name the same type. The user's file name never
reaches the path: it is kept, sanitized, as the display name.

ZIP archives are inspected without being extracted. The end-of-central-
directory record is checked before parsing, so a huge central directory, too
many entries or ZIP64 counts and sizes are refused cheaply; an archive
declaring too large an uncompressed total is refused as a likely ZIP bomb, and
at most a few bytes of one entry (ODT's ``mimetype``) are ever decompressed.

``open_stored`` and ``remove`` accept only a plain stored name, so no path,
traversal or symbolic link reaches the file system.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import stat
import tempfile
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from typing import BinaryIO
from urllib.parse import quote

from .errors import ValidationError

CHUNK_SIZE = 64 * 1024
NAME_MAX = 150  # display names are capped (the column holds 255)
MAX_ZIP_ENTRIES = 5_000
MAX_ZIP_DIRECTORY = 2 * 1024 * 1024  # bytes of central directory parsed at most
MAX_ZIP_UNCOMPRESSED = 512 * 1024 * 1024  # declared total; above it, a likely bomb

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ODT = "application/vnd.oasis.opendocument.text"
MIME_BY_EXTENSION = {"pdf": PDF, "docx": DOCX, "xlsx": XLSX, "odt": ODT}
ACCEPT = ",".join(f".{extension}" for extension in MIME_BY_EXTENSION)  # for <input accept>

UNSUPPORTED = "Solo se admiten ficheros PDF, DOCX, XLSX u ODT."
UNRECOGNIZED = "El contenido del fichero no es un PDF, DOCX, XLSX u ODT válido."
EXTENSION_MISMATCH = "La extensión del fichero no corresponde a su contenido."
EMPTY = "El fichero está vacío."

STORED_NAME = re.compile(r"[0-9a-f]{32}")
_ODT_MIMETYPE = ODT.encode("ascii")
_PDF_MAGIC = b"%PDF-"
_ZIP_LOCAL_HEADER = b"PK\x03\x04"
# The end-of-central-directory record (PKWARE APPNOTE 4.3.16): its fixed part,
# then a comment of at most 0xFFFF bytes. Fields as (offset, length) in it.
_ZIP_END_RECORD = b"PK\x05\x06"
_ZIP_END_SIZE = 22
_ZIP_END_MAX_COMMENT = 0xFFFF
_ZIP_END_DISK_ENTRIES = (8, 2)  # entries on this disk
_ZIP_END_ENTRIES = (10, 2)  # entries in the whole archive
_ZIP_END_DIRECTORY_SIZE = (12, 4)
# A field too small for its value holds all ones and defers to the ZIP64 records.
_ZIP64_MARKERS = ((_ZIP_END_DISK_ENTRIES, 0xFFFF), (_ZIP_END_ENTRIES, 0xFFFF),
                  (_ZIP_END_DIRECTORY_SIZE, 0xFFFFFFFF))
_UNSAFE_FALLBACK = re.compile(r"[^A-Za-z0-9._ ()-]")


@dataclass(frozen=True)
class StoredFile:
    """A file accepted by ``store``: what a revision records about its attachment."""

    stored_name: str  # the random name on disk
    display_name: str  # the sanitized name the user gave it
    size: int
    sha256: str
    mime: str


@dataclass(frozen=True)
class StoredEntry:
    """One stored file found on disk by ``stored_names``."""

    name: str
    modified: float  # seconds since the epoch


@dataclass(frozen=True)
class Listing:
    """The stored files of a directory and how many other entries it holds."""

    stored: list[StoredEntry]
    ignored: int


def human_size(size: int) -> str:
    """``size`` bytes for people, with a decimal comma: ``48 B``, ``1,5 KB``, ``20 MB``."""
    for unit, scale in (("MB", 1024 * 1024), ("KB", 1024)):
        if size >= scale:
            number = f"{size / scale:.1f}".rstrip("0").rstrip(".").replace(".", ",")
            return f"{number} {unit}"
    return f"{size} B"


def too_large(max_bytes: int) -> str:
    """The message for a file above ``max_bytes``."""
    return f"El fichero supera el tamaño máximo de {human_size(max_bytes)}."


def display_name(original_name: str | None) -> str:
    """The user's file name without path components or control characters, capped.

    Returns ``""`` when nothing usable is left.
    """
    text = unicodedata.normalize("NFC", original_name or "")
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = "".join(c for c in text if unicodedata.category(c)[0] != "C")
    text = text.strip().lstrip(".").strip()
    if len(text) <= NAME_MAX:
        return text
    stem, dot, extension = text.rpartition(".")
    if dot and 0 < len(extension) <= 10:
        return stem[: NAME_MAX - len(extension) - 1] + "." + extension
    return text[:NAME_MAX]


def content_disposition(name: str) -> str:
    """An ``attachment`` header naming ``name`` (RFC 6266 with an RFC 5987 ``filename*``)."""
    fallback = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    fallback = _UNSAFE_FALLBACK.sub("_", fallback).strip() or "documento"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def store(base_dir: str, stream: BinaryIO, original_name: str | None, *,
          max_bytes: int) -> StoredFile:
    """Check and keep an uploaded file; raise ``ValidationError`` if it is refused.

    Nothing stays in ``base_dir`` unless the file is accepted: the temporary
    file is deleted on any failure, including errors reading ``stream``.
    """
    name = display_name(original_name)
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    expected = MIME_BY_EXTENSION.get(extension)
    if expected is None:
        raise ValidationError(UNSUPPORTED)
    handle, temporary = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=base_dir)
    try:
        size, digest = _copy(handle, stream, max_bytes)
        if size == 0:
            raise ValidationError(EMPTY)
        detected = _detect(temporary)
        if detected is None:
            raise ValidationError(UNRECOGNIZED)
        if detected != expected:
            raise ValidationError(EXTENSION_MISMATCH)
        stored_name = uuid.uuid4().hex
        os.replace(temporary, os.path.join(base_dir, stored_name))
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
    return StoredFile(stored_name, name, size, digest, detected)


def open_stored(base_dir: str, stored_name: str) -> BinaryIO:
    """Open a stored file for reading.

    Raises ``ValueError`` for anything but a plain stored name and
    ``FileNotFoundError`` when no regular file has that name (a symbolic link
    or a directory counts as missing).
    """
    path = _path(base_dir, stored_name)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise FileNotFoundError(stored_name) from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise FileNotFoundError(stored_name)
    return os.fdopen(descriptor, "rb")


def remove(base_dir: str, stored_name: str) -> bool:
    """Delete one stored file; ``False`` if it was already gone."""
    try:
        os.unlink(_path(base_dir, stored_name))
    except FileNotFoundError:
        return False
    return True


def stored_names(base_dir: str) -> Listing:
    """The stored files in ``base_dir`` (regular files with a stored name) and the rest."""
    stored, ignored = [], 0
    with os.scandir(base_dir) as entries:
        for entry in entries:
            if STORED_NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                stored.append(StoredEntry(entry.name, entry.stat(follow_symlinks=False).st_mtime))
            else:
                ignored += 1
    return Listing(sorted(stored, key=lambda item: item.name), ignored)


def _path(base_dir: str, stored_name: str) -> str:
    if not isinstance(stored_name, str) or not STORED_NAME.fullmatch(stored_name):
        raise ValueError("Not a stored file name.")
    return os.path.join(base_dir, stored_name)


def _copy(handle: int, stream: BinaryIO, max_bytes: int) -> tuple[int, str]:
    """Copy ``stream`` into the open descriptor ``handle``; return the size and SHA-256."""
    digest, size = hashlib.sha256(), 0
    with os.fdopen(handle, "wb") as out:
        while chunk := stream.read(CHUNK_SIZE):
            size += len(chunk)
            if size > max_bytes:
                raise ValidationError(too_large(max_bytes))
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    return size, digest.hexdigest()


def _detect(path: str) -> str | None:
    """The MIME type recognized from the file's content, or ``None``."""
    with open(path, "rb") as handle:
        head = handle.read(len(_PDF_MAGIC))
        if head == _PDF_MAGIC:
            return PDF
        if not head.startswith(_ZIP_LOCAL_HEADER) or not _zip_directory_is_small(handle):
            return None
    try:
        with zipfile.ZipFile(path) as archive:
            return _office_type(archive)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError, RuntimeError,
            NotImplementedError, ValueError):
        return None


def _zip_directory_is_small(handle: BinaryIO) -> bool:
    """Whether the end record announces few entries and a small central directory.

    Read before ``zipfile`` parses (and keeps in memory) the whole directory.
    """
    size = handle.seek(0, os.SEEK_END)
    tail_size = min(size, _ZIP_END_SIZE + _ZIP_END_MAX_COMMENT)
    handle.seek(size - tail_size)
    tail = handle.read(tail_size)
    at = tail.rfind(_ZIP_END_RECORD)
    if at < 0 or len(tail) - at < _ZIP_END_SIZE:
        return False
    record = tail[at:at + _ZIP_END_SIZE]
    if any(_number(record, field) == marker for field, marker in _ZIP64_MARKERS):
        return False  # no accepted document needs ZIP64, whatever the limits
    entries = _number(record, _ZIP_END_ENTRIES)
    return (0 < entries <= MAX_ZIP_ENTRIES
            and _number(record, _ZIP_END_DIRECTORY_SIZE) <= MAX_ZIP_DIRECTORY)


def _number(record: bytes, field: tuple[int, int]) -> int:
    """The little-endian number stored at ``field`` (offset, length) of ``record``."""
    offset, length = field
    return int.from_bytes(record[offset:offset + length], "little")


def _office_type(archive: zipfile.ZipFile) -> str | None:
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ZIP_ENTRIES:
        return None
    if sum(entry.file_size for entry in entries) > MAX_ZIP_UNCOMPRESSED:
        return None
    first = entries[0]
    if first.filename == "mimetype":
        with archive.open(first) as handle:  # bounded read: never the whole entry
            return ODT if handle.read(len(_ODT_MIMETYPE) + 1) == _ODT_MIMETYPE else None
    names = [entry.filename for entry in entries]
    if "[Content_Types].xml" not in names:
        return None
    word = any(name.startswith("word/") for name in names)
    sheet = any(name.startswith("xl/") for name in names)
    if word == sheet:  # neither, or an ambiguous mix of both
        return None
    return DOCX if word else XLSX
