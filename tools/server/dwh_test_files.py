"""I12-07: test files of the file source PV_FILES on the DWH server (plan I12, decision F10).

  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py               show what will be written
  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py --apply       folder + base set of files
  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py --add NAME    one more file for the acceptance:
        next   svc_inc_4.json, dated after the base set, 3 rows changed      (loaded)
        late   svc_inc_late.json, dated before the base set                  (NOT_ACTUAL)
        dup    svc_inc_copy.json, same contents as svc_inc_3.json            (not loaded again)
        nodate svc_inc_nodate.json, no actuality date                        (NO_DATE)
        broken svc_inc_broken.json, cut in the middle                        (FILE_BROKEN)
        dtd    svc_xml_dtd.xml, XML with DOCTYPE and an entity               (FILE_XML_DTD_FORBIDDEN)
        big    svc_inc_big.json, larger than the size limit (sparse file)    (FILE_TOO_LARGE)
  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py --check       list the files
  sudo python3 /opt/dwh/current/src/tools/server/dwh_test_files.py --remove      delete the svc_* files of the folder

Base set (--apply), in /srv/dwh/files/PV_FILES (folder root:dwh 0750, files root:dwh 0640 - dwh only reads):
  svc_full_1..3.json   three snapshots of public.services, actuality dates D-2, D-1, D (09:00 Asia/Baku);
                       2 and 3 change the description of 3 rows each   -> object with mode LATEST: only 3 is loaded
  svc_inc_1..3.json    the same three files under another mask          -> object with mode ALL_NEW: 1, 2, 3 in order
  svc_xml_1..2.xml     XML (export.items.service, date export.header.actual_at), D-1 and D
  svc_jsonl_1.jsonl    JSON Lines (date in the first line: actual_at), D
JSON: records at data.items, date at meta.actual_at. The rows come from the test API service of I11-05
(127.0.0.1:<PV_API_PORT>, token from /etc/dwh/test-api.env): no database password is used here.
Only this tool writes the folder; DWH services only read it. Prints TEST_FILES_RESULT: PASS / FAIL / PLAN.
Uses the system python3 only.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("DWH_FILE_ROOT") or "/srv/dwh/files")
CODE = "PV_FILES"
ENV_FILE = Path("/etc/dwh/test-api.env")
BAKU = timezone(timedelta(hours=4))
DEFAULT_MAX_BYTES = 200 * 2**20
ADDS = ("next", "late", "dup", "nodate", "broken", "dtd", "big")


class ToolError(Exception):
    pass


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"')
    return values


def fetch_services(env: dict[str, str], opener=urllib.request.urlopen) -> list[dict]:
    """All rows of public.services through the test API service (page pagination, JSON)."""
    token, port = env.get("PV_API_TOKEN"), env.get("PV_API_PORT", "8090")
    if not token:
        raise ToolError(f"no PV_API_TOKEN in {ENV_FILE}: set up the test API service first (dwh_test_api_setup.py)")
    rows: list[dict] = []
    for page in range(1, 1000):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/services?page={page}&size=500",
                                         headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        try:
            with opener(request, timeout=30) as answer:
                items = json.loads(answer.read().decode("utf-8"))["data"]["items"]
        except Exception as exc:  # noqa: BLE001 - shown as a short reason, no token
            raise ToolError(f"test API service did not answer ({type(exc).__name__})") from None
        rows.extend(items)
        if len(items) < 500:
            return rows
    raise ToolError("too many pages")


def dates(today: datetime | None = None) -> list[datetime]:
    """D-2, D-1, D at 09:00 Asia/Baku."""
    today = (today or datetime.now(BAKU)).astimezone(BAKU).replace(hour=9, minute=0, second=0, microsecond=0)
    return [today - timedelta(days=2), today - timedelta(days=1), today]


def changed(rows: list[dict], version: int) -> list[dict]:
    """Version 1 is the rows as they are; each next version changes the description of 3 more rows."""
    out = copy.deepcopy(rows)
    for row in out[: 3 * (version - 1)]:
        row["description"] = f"{row.get('description') or ''} (file v{version})".strip()
    return out


def json_doc(rows: list[dict], at: datetime | None) -> bytes:
    meta = {"source": "PROCESS_VIEWER public.services", "rows": len(rows)}
    if at is not None:
        meta["actual_at"] = at.isoformat()
    return json.dumps({"meta": meta, "data": {"items": rows}}, ensure_ascii=False, indent=1).encode("utf-8")


def jsonl_doc(rows: list[dict], at: datetime) -> bytes:
    lines = [json.dumps({**row, "actual_at": at.isoformat()} if i == 0 else row, ensure_ascii=False)
             for i, row in enumerate(rows)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def xml_doc(rows: list[dict], at: datetime) -> bytes:
    root = ET.Element("export")
    ET.SubElement(ET.SubElement(root, "header"), "actual_at").text = at.isoformat()
    holder = ET.SubElement(root, "items")
    for row in rows:
        node = ET.SubElement(holder, "service")
        for key, value in row.items():
            child = ET.SubElement(node, key)
            if value is None:
                child.set("null", "true")
            elif isinstance(value, (dict, list)):
                child.text = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool):
                child.text = "true" if value else "false"
            else:
                child.text = str(value)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def base_set(rows: list[dict], days: list[datetime]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for n, at in enumerate(days, 1):
        files[f"svc_full_{n}.json"] = json_doc(changed(rows, n), at)
        files[f"svc_inc_{n}.json"] = json_doc(changed(rows, n), at)
    files["svc_xml_1.xml"] = xml_doc(changed(rows, 2), days[1])
    files["svc_xml_2.xml"] = xml_doc(changed(rows, 3), days[2])
    files["svc_jsonl_1.jsonl"] = jsonl_doc(changed(rows, 3), days[2])
    return files


def added(name: str, rows: list[dict], days: list[datetime], folder: Path) -> tuple[str, bytes | int]:
    """(file name, contents) or (file name, size of a sparse file) for --add."""
    if name == "next":
        return "svc_inc_4.json", json_doc(changed(rows, 4), days[2] + timedelta(hours=3))
    if name == "late":
        return "svc_inc_late.json", json_doc(changed(rows, 1), days[0] - timedelta(days=1))
    if name == "dup":
        source = folder / "svc_inc_3.json"
        if not source.is_file():
            raise ToolError("svc_inc_3.json is missing: run --apply first")
        return "svc_inc_copy.json", source.read_bytes()
    if name == "nodate":
        return "svc_inc_nodate.json", json_doc(rows, None)
    if name == "broken":
        body = json_doc(rows, days[2] + timedelta(hours=4))
        return "svc_inc_broken.json", body[: len(body) // 2]
    if name == "dtd":
        return "svc_xml_dtd.xml", (b'<?xml version="1.0"?>\n<!DOCTYPE export [<!ENTITY a "aaaaaaaaaa">]>\n'
                                   b"<export><header><actual_at>2030-01-01</actual_at></header>"
                                   b"<items><service><id>&a;</id></service></items></export>")
    if name == "big":
        try:
            limit = int(os.environ.get("DWH_FILE_MAX_BYTES") or DEFAULT_MAX_BYTES)
        except ValueError:
            limit = DEFAULT_MAX_BYTES
        return "svc_inc_big.json", limit + 2**20
    raise ToolError(f"unknown file '{name}'")


def owner_group() -> tuple[int, int] | None:
    try:
        import grp

        return 0, grp.getgrnam("dwh").gr_gid
    except (ImportError, KeyError):
        return None


def prepare_folder(folder: Path) -> None:
    folder.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    folder.mkdir(mode=0o750, exist_ok=True)
    ids = owner_group()
    if ids and os.geteuid() == 0:
        os.chown(folder, *ids)
    os.chmod(folder, 0o750)


def put(folder: Path, name: str, body: bytes | int) -> None:
    path = folder / name
    tmp = folder / f".{name}.tmp"
    with open(tmp, "wb") as handle:
        if isinstance(body, int):
            handle.truncate(body)  # sparse: takes no disk space
        else:
            handle.write(body)
    ids = owner_group()
    if ids and os.geteuid() == 0:
        os.chown(tmp, *ids)
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)  # appears complete: never half-written for the loads


def listing(folder: Path) -> list[str]:
    if not folder.is_dir():
        return [f"{folder}: missing"]
    out = [f"{folder}: {oct(folder.stat().st_mode & 0o777)}"]
    for p in sorted(folder.iterdir()):
        st = p.stat()
        out.append(f"  {p.name:28} {st.st_size:>12} bytes  {oct(st.st_mode & 0o777)}")
    return out


def main(argv: list[str] | None = None, *, root: Path | None = None, env_file: Path | None = None,
         opener=urllib.request.urlopen, today: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="I12-07 test files of PV_FILES")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--add", choices=ADDS)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)
    folder = (root or ROOT) / CODE
    try:
        if args.check:
            print("\n".join(listing(folder)))
            print("TEST_FILES_RESULT: PASS" if folder.is_dir() else "TEST_FILES_RESULT: FAIL")
            return 0 if folder.is_dir() else 1
        if args.remove:
            removed = 0
            if folder.is_dir():
                for p in folder.glob("svc_*"):
                    p.unlink()
                    removed += 1
            print(f"removed {removed} file(s) from {folder}")
            print("TEST_FILES_RESULT: PASS")
            return 0
        days = dates(today)
        if not args.apply and not args.add:
            print(f"PLAN: folder {folder} (root:dwh 0750), files root:dwh 0640")
            print("  svc_full_1..3.json, svc_inc_1..3.json  dates " + ", ".join(d.isoformat() for d in days))
            print("  svc_xml_1..2.xml, svc_jsonl_1.jsonl     rows from the test API service (I11-05)")
            print("TEST_FILES_RESULT: PLAN (run with --apply)")
            return 0
        rows = [] if args.add in ("dtd", "big", "dup") else fetch_services(read_env(env_file or ENV_FILE), opener)
        if not args.add and not rows:
            raise ToolError("the test API service returned no rows")
        prepare_folder(folder)
        files = base_set(rows, days) if args.apply else dict([added(args.add, rows, days, folder)])
        for name, body in files.items():
            put(folder, name, body)
            print(f"written {name}" + (f" ({len(rows)} rows)" if rows and isinstance(body, bytes) else ""))
        print("\n".join(listing(folder)))
        print("TEST_FILES_RESULT: PASS")
        return 0
    except (ToolError, OSError) as exc:
        print(f"ERROR: {exc}")
        print("TEST_FILES_RESULT: FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
