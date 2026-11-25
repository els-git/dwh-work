"""I12: files of a file source (plan docs/DWH_I12_FILE_CONNECTOR_PLAN_APPROVED_20261125.md).

DWH only READS files: the folder of a source is <DWH_FILE_ROOT>/<code of the source> on the DWH server
(F1, default /srv/dwh/files); it is not a setting, so no path comes from a user. Only regular files directly in
that folder whose name matches the mask are seen; a file (or a link) that resolves outside the folder is ignored.

Formats (F3): JSON (records at a path, the actuality date at a path of the same document), JSONL (one record per
line; the date is read from the FIRST line by its path) and XML (records at an element path; DOCTYPE and entities
are refused, F8). The actuality date is inside the file (F2'/F4'/F5'): a file without it is not loaded.

Nothing here writes, renames or deletes a file.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FORMATS = ("JSON", "JSONL", "XML")
MODES = ("LATEST", "ALL_NEW")
MASK = re.compile(r"^[A-Za-z0-9_.*?\[\]-]{1,200}$")
PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,199}$")
CODE = re.compile(r"^[A-Za-z0-9_]{1,63}$")
DEFAULT_ROOT = "/srv/dwh/files"
DEFAULT_MAX_BYTES = 200 * 2**20
MAX_DEPTH = 64
MAX_FILES = 10_000


class FileSourceError(Exception):
    """A refusal with a stable code; the message names files and settings, never contents of a file."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ------------------------------------------------------------------ settings


def normalize(doc: dict | None, draft: bool = False) -> dict:
    """Checked settings of a file object. draft=True lets an unfinished form be saved (mask and paths may be empty)."""
    doc = doc or {}
    if not isinstance(doc, dict):
        raise FileSourceError("FILE_CONFIG_INVALID", "File settings must be an object")
    fmt = str(doc.get("format") or "JSON").upper()
    if fmt not in FORMATS:
        raise FileSourceError("FILE_CONFIG_INVALID", "Format: JSON, JSONL or XML")
    mode = str(doc.get("mode") or "LATEST").upper()
    if mode not in MODES:
        raise FileSourceError("FILE_CONFIG_INVALID", "Mode: LATEST or ALL_NEW")
    mask = str(doc.get("mask") or "").strip()
    if not MASK.match(mask) or mask in (".", ".."):
        if draft and not mask:
            mask = "*.json" if fmt != "XML" else "*.xml"
        else:
            raise FileSourceError("FILE_CONFIG_INVALID", "Mask: latin letters, digits, _ . - and * ? [ ], no folders")
    records_path = str(doc.get("records_path") or "").strip()
    if records_path and not all(PATH.match(p) for p in [records_path]):
        raise FileSourceError("FILE_CONFIG_INVALID", "Path to the records: names separated by dots")
    if fmt == "XML" and not records_path and not draft:
        raise FileSourceError("FILE_CONFIG_INVALID", "XML: set the path to the record element")
    date_path = str(doc.get("date_path") or "").strip()
    if date_path and not PATH.match(date_path):
        raise FileSourceError("FILE_CONFIG_INVALID", "Path to the date: names separated by dots")
    if not date_path and not draft:
        raise FileSourceError("FILE_CONFIG_INVALID", "Set the path to the actuality date inside the file")
    zone = str(doc.get("time_zone") or "Asia/Baku").strip()
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise FileSourceError("FILE_CONFIG_INVALID", f"Unknown time zone '{zone[:60]}'") from None
    try:
        wait = int(doc.get("wait_seconds", 60))
    except (TypeError, ValueError):
        raise FileSourceError("FILE_CONFIG_INVALID", "Wait for a file being written: seconds") from None
    if not 0 <= wait <= 3600:
        raise FileSourceError("FILE_CONFIG_INVALID", "Wait for a file being written: 0 to 3600 seconds")
    return {"mask": mask, "format": fmt, "records_path": records_path if fmt != "JSONL" else "",
            "date_path": date_path, "mode": mode, "time_zone": zone, "wait_seconds": wait}


def file_root() -> Path:
    return Path(os.environ.get("DWH_FILE_ROOT") or DEFAULT_ROOT)


def max_bytes() -> int:
    try:
        return max(1, int(os.environ.get("DWH_FILE_MAX_BYTES") or DEFAULT_MAX_BYTES))
    except ValueError:
        return DEFAULT_MAX_BYTES


def source_folder(code: str, root: Path | None = None) -> Path:
    """Folder of a source: <root>/<code>. Refused when the code is not a plain name or the folder is missing."""
    if not CODE.match(code or ""):
        raise FileSourceError("FILE_FOLDER_INVALID", "Source code cannot name a folder")
    base = (root or file_root()).resolve()
    folder = (base / code).resolve()
    if folder.parent != base:
        raise FileSourceError("FILE_FOLDER_INVALID", "Folder of the source is outside the file root")
    if not folder.is_dir():
        raise FileSourceError("FILE_FOLDER_MISSING", f"Folder {base}/{code} does not exist on the DWH server")
    return folder


# ------------------------------------------------------------------ listing and reading


@dataclass
class FileEntry:
    name: str
    path: Path
    size: int
    mtime: float


def list_files(folder: Path, mask: str) -> list[FileEntry]:
    """Regular files directly in the folder matching the mask (case-sensitive), by name."""
    found: list[FileEntry] = []
    try:
        names = sorted(os.listdir(folder))
    except OSError as exc:
        raise FileSourceError("FILE_FOLDER_UNREADABLE", f"Folder cannot be read ({exc.strerror})") from None
    for name in names:
        if not fnmatch.fnmatchcase(name, mask) or name.startswith("."):
            continue
        path = folder / name
        try:
            real = path.resolve(strict=True)
            st = real.stat()
        except (OSError, RuntimeError):
            continue
        if real.parent != folder or not stat.S_ISREG(st.st_mode):
            continue  # a link out of the folder, a folder, a device: not a file of the source
        found.append(FileEntry(name=name, path=real, size=st.st_size, mtime=st.st_mtime))
        if len(found) > MAX_FILES:
            raise FileSourceError("FILE_TOO_MANY", f"More than {MAX_FILES} files match the mask")
    return found


def read_file(entry: FileEntry, limit: int | None = None) -> tuple[bytes, str]:
    """Contents and SHA-256 (lower case hex). Refused above the size limit."""
    limit = limit or max_bytes()
    if entry.size > limit:
        raise FileSourceError("FILE_TOO_LARGE", f"File is {entry.size // 2**20} MB, the limit is {limit // 2**20} MB")
    try:
        with open(entry.path, "rb") as handle:
            body = handle.read(limit + 1)
    except OSError as exc:
        raise FileSourceError("FILE_UNREADABLE", f"File cannot be read ({exc.strerror})") from None
    if len(body) > limit:
        raise FileSourceError("FILE_TOO_LARGE", f"File is larger than {limit // 2**20} MB")
    return body, hashlib.sha256(body).hexdigest()


def hash_file(entry: FileEntry) -> str:
    """SHA-256 of a file read in parts (for a file above the size limit: its content is still registered)."""
    digest = hashlib.sha256()
    try:
        with open(entry.path, "rb") as handle:
            for part in iter(lambda: handle.read(2**20), b""):
                digest.update(part)
    except OSError as exc:
        raise FileSourceError("FILE_UNREADABLE", f"File cannot be read ({exc.strerror})") from None
    return digest.hexdigest()


# ------------------------------------------------------------------ parsing


@dataclass
class ParsedFile:
    records: list[dict]
    actual_at: datetime | None
    date_text: str | None = None
    notes: list[str] = field(default_factory=list)


def _path(node: Any, path: str) -> Any:
    if not path:
        return node
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _depth_ok(value: Any) -> bool:
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            return False
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return True


def _xml_value(el: ET.Element) -> Any:
    if el.get("null") == "true":
        return None
    children = list(el)
    if not children:
        return el.text if el.text is not None else ""
    out: dict[str, Any] = {}
    for c in children:
        value = _xml_value(c)
        if c.tag in out:
            out[c.tag] = (out[c.tag] if isinstance(out[c.tag], list) else [out[c.tag]]) + [value]
        else:
            out[c.tag] = value
    return out


_DMY = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$")


def parse_date(value: Any, zone: str) -> datetime | None:
    """Actuality date: ISO 8601 date or date-time (with or without offset), or DD.MM.YYYY[ HH:MM[:SS]].
    Without an offset the time zone of the settings applies. Anything else: no date."""
    if value is None or isinstance(value, (bool, dict, list)):
        return None
    text = str(value).strip()
    if not text:
        return None
    tz = ZoneInfo(zone)
    m = _DMY.match(text)
    try:
        if m:
            d, mo, y, h, mi, s = m.groups()
            result = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            result = datetime.combine(date.fromisoformat(text), time())
        elif re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text):
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            return None
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=tz)
    return result.astimezone(timezone.utc)


def _xml_find(root: ET.Element, path: str) -> list[ET.Element]:
    parts = path.split(".") if path else []
    if parts and parts[0] == root.tag:
        parts = parts[1:]
    return root.findall("/".join(parts)) if parts else [root]


def parse_file(body: bytes, cfg: dict) -> ParsedFile:
    """Records (flat or nested dicts) and the actuality date of one file. Broken contents: FILE_BROKEN."""
    fmt = cfg["format"]
    if fmt == "XML":
        if b"<!DOCTYPE" in body or b"<!ENTITY" in body:  # XML names are case-sensitive
            raise FileSourceError("FILE_XML_DTD_FORBIDDEN", "XML with DOCTYPE or entities is not accepted")
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise FileSourceError("FILE_BROKEN", "File is not valid XML") from None
        depth = [(root, 1)]
        while depth:
            el, d = depth.pop()
            if d > MAX_DEPTH:
                raise FileSourceError("FILE_BROKEN", f"XML is nested deeper than {MAX_DEPTH} levels")
            depth.extend((c, d + 1) for c in el)
        records = []
        for el in _xml_find(root, cfg["records_path"]):
            value = _xml_value(el)
            records.append(value if isinstance(value, dict) else {el.tag: value})
        date_text = None
        if cfg["date_path"]:
            found = _xml_find(root, cfg["date_path"])
            if found:
                date_text = found[0].get("value") or (found[0].text or "").strip() or None
        return ParsedFile(records=records, actual_at=parse_date(date_text, cfg["time_zone"]), date_text=date_text)
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileSourceError("FILE_BROKEN", "File is not UTF-8 text") from None
    if fmt == "JSONL":
        records = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line, parse_float=Decimal)
            except ValueError:
                raise FileSourceError("FILE_BROKEN", f"Line {number} is not JSON") from None
            if not isinstance(item, dict) or not _depth_ok(item):
                raise FileSourceError("FILE_BROKEN", f"Line {number} is not a JSON object")
            records.append(item)
        date_value = _path(records[0], cfg["date_path"]) if records and cfg["date_path"] else None
        return ParsedFile(records=records, actual_at=parse_date(date_value, cfg["time_zone"]),
                          date_text=None if date_value is None else str(date_value))
    try:
        data = json.loads(text, parse_float=Decimal)
    except (ValueError, RecursionError):
        raise FileSourceError("FILE_BROKEN", "File is not valid JSON") from None
    if not _depth_ok(data):
        raise FileSourceError("FILE_BROKEN", f"JSON is nested deeper than {MAX_DEPTH} levels")
    found = _path(data, cfg["records_path"])
    if isinstance(found, dict):
        found = [found]
    if not isinstance(found, list) or any(not isinstance(r, dict) for r in found):
        raise FileSourceError("FILE_BAD_RECORDS",
                              f"Path '{cfg['records_path'] or '(root)'}' is not a list of objects")
    date_value = _path(data, cfg["date_path"]) if cfg["date_path"] else None
    return ParsedFile(records=found, actual_at=parse_date(date_value, cfg["time_zone"]),
                      date_text=None if date_value is None else str(date_value)[:100])
