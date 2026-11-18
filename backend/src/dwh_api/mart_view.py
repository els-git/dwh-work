"""Viewing DM marts in "Reporting" (I22 step 3; package 022, plan docs/DWH_I22_MART_VIEW_PLAN_APPROVED_20261118.md).

V1  "Reporting" -> "Marts": the marts of the whitelist the role may open (REPORT_VIEW and the permission of the
    mart; decided by the database, app.mart_view_list).
V2  The client never sends SQL or a table name. The database names the table only after its access check
    (app.mart_view_begin / _continue); the backend builds the query itself from the columns of that table
    (pg_catalog), quoted identifiers and parameters for the values, and runs it as dwh_ai_ro (schema dm only,
    read-only, statement timeout).
V3  Pages of 100 rows, total count, sort by a column, filters per column: equal, contains (text), from / to,
    empty / not empty.
V4  Export CSV / XLSX up to 1 000 000 rows, PDF up to 5 000 rows; more rows -> no file, a structured refusal with
    the number of rows, the limit and what to do (plan, section 3.1).
V5  Every opening (filters and sort) and every export is a row MART_VIEW of app.ai_request_log, without rows.
    Further pages reuse the row of the opening; their filters and sort are read back from the database.
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterator, Literal
from uuid import uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from dwh_api.config import get_api_config
from dwh_api.db import connect_ai_readonly, connect_control, connect_readonly
from dwh_api.integration_admin import _db_message, _raise_database_error, _session_token_hash, _set_request_context

config = get_api_config()
router = APIRouter(prefix="/api/reports/marts", tags=["Mart view"])

PAGE_SIZE = 100
EXPORT_LIMITS = {"CSV": 1_000_000, "XLSX": 1_000_000, "PDF": 5_000}
FETCH_ROWS = 5_000
PDF_COLUMNS_PER_TABLE = 10
PDF_CELL_CHARS = 40
STATEMENT_TIMEOUT_MS = 60_000
MIME = {"CSV": "text/csv; charset=utf-8",
        "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "PDF": "application/pdf"}

FilterOp = Literal["eq", "contains", "gte", "lte", "empty", "not_empty"]


class ColumnFilter(BaseModel):
    column: str = Field(min_length=1, max_length=63)
    op: FilterOp
    value: str | None = Field(default=None, max_length=200)


class Sort(BaseModel):
    column: str = Field(min_length=1, max_length=63)
    desc: bool = False


class ViewRequest(BaseModel):
    filters: list[ColumnFilter] = Field(default_factory=list, max_length=20)
    sort: Sort | None = None


class PageRequest(BaseModel):
    page: int = Field(ge=1, le=1_000_000)


class ExportRequest(ViewRequest):
    format: Literal["CSV", "XLSX", "PDF"]


@dataclass(frozen=True)
class Column:
    name: str
    type: str       # format_type(), from pg_catalog, used in casts
    kind: str       # number | date | boolean | text


class MartViewError(Exception):
    """A request the user can correct (bad filter, too many rows); status and payload for the client."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.extra = extra


# ------------------------------------------------------------------ database calls (dwh_app, functions of 022)

def _call(token_hash: str, statement: str, params: tuple = (), fetch: Literal["one", "all", "none"] = "one") -> Any:
    """One call of a 022 function in its own committed transaction (dwh_app; the rights are checked there)."""
    try:
        with closing(connect_control(config)) as conn:
            try:
                with conn.cursor() as cur:
                    _set_request_context(cur, str(uuid4()))
                    cur.execute(statement, (token_hash, *params))
                    result = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else None
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    except psycopg.Error as exc:
        message = _db_message(exc)
        lower = message.lower()
        if "access denied" in lower:
            raise HTTPException(status_code=403, detail=message) from exc
        if "not found" in lower or "expired" in lower:
            raise HTTPException(status_code=404, detail=message) from exc
        _raise_database_error(exc)
    return result


def _finish(token_hash: str, request_id: int, status: str, code: str | None, rows: int | None,
            message: str | None) -> None:
    _call(token_hash, "SELECT app.mart_view_finish(%s, %s, %s, %s, %s, %s)",
          (request_id, status, code, rows, message), fetch="none")


def _ai_password() -> str:
    password = os.environ.get("DWH_AI_DB_PASSWORD", "").strip()
    if not password:
        raise HTTPException(status_code=503, detail="mart view is not configured (DWH_AI_DB_PASSWORD)")
    return password


# ------------------------------------------------------------------ query building (pure)

def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def column_kind(type_name: str, category: str) -> str:
    if category == "N":
        return "number"
    if category == "D":
        return "date"
    if category == "B":
        return "boolean"
    return "text"


def read_columns(cur, schema: str, table: str) -> list[Column]:
    cur.execute(
        "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, t.typcategory AS category "
        "FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid "
        "WHERE a.attrelid = to_regclass(format('%%I.%%I', %s::text, %s::text)) AND a.attnum > 0 AND NOT a.attisdropped "
        "ORDER BY a.attnum", (schema, table))
    return [Column(r["name"], r["type"], column_kind(r["type"], r["category"])) for r in cur.fetchall()]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def where_clause(columns: list[Column], filters: list[ColumnFilter]) -> tuple[str, list[Any]]:
    by_name = {c.name: c for c in columns}
    parts: list[str] = []
    params: list[Any] = []
    for f in filters:
        col = by_name.get(f.column)
        if col is None:
            raise MartViewError(400, "MART_FILTER_COLUMN", f"unknown column {f.column}", column=f.column)
        q = _qi(col.name)
        if f.op in ("eq", "contains", "gte", "lte") and (f.value is None or f.value.strip() == ""):
            raise MartViewError(400, "MART_FILTER_VALUE", f"filter on {col.name} needs a value", column=col.name)
        if f.op == "eq":
            if col.kind == "text":
                parts.append(f"{q}::text = %s")
            else:
                parts.append(f"{q} = CAST(%s AS {col.type})")
            params.append(f.value.strip() if col.kind != "text" else f.value)
        elif f.op == "contains":
            parts.append(f"{q}::text ILIKE %s")
            params.append("%" + _escape_like(f.value) + "%")
        elif f.op in ("gte", "lte"):
            if col.kind not in ("number", "date"):
                raise MartViewError(400, "MART_FILTER_OPERATOR", f"from / to needs a number or date column ({col.name})",
                                    column=col.name)
            parts.append(f"{q} {'>=' if f.op == 'gte' else '<='} CAST(%s AS {col.type})")
            params.append(f.value.strip())
        elif f.op == "empty":
            parts.append(f"({q} IS NULL OR {q}::text = '')" if col.kind == "text" else f"{q} IS NULL")
        else:
            parts.append(f"({q} IS NOT NULL AND {q}::text <> '')" if col.kind == "text" else f"{q} IS NOT NULL")
    return (" WHERE " + " AND ".join(parts)) if parts else "", params


def order_clause(columns: list[Column], sort: Sort | None) -> str:
    if not columns:
        raise MartViewError(400, "MART_NO_COLUMNS", "the mart has no columns")
    if sort is None:
        return f" ORDER BY {_qi(columns[0].name)}"
    names = {c.name for c in columns}
    if sort.column not in names:
        raise MartViewError(400, "MART_SORT_COLUMN", f"unknown column {sort.column}", column=sort.column)
    first = _qi(columns[0].name)
    tie = "" if sort.column == columns[0].name else f", {first}"
    return f" ORDER BY {_qi(sort.column)} {'DESC' if sort.desc else 'ASC'} NULLS LAST{tie}"


def select_sql(schema: str, table: str, columns: list[Column], request: ViewRequest) -> tuple[str, str, list[Any]]:
    """(count statement, ordered select statement, parameters) for the mart and the filters."""
    where, params = where_clause(columns, request.filters)
    source = f"{_qi(schema)}.{_qi(table)}"
    cols = ", ".join(_qi(c.name) for c in columns)
    return (f"SELECT count(*) AS n FROM {source}{where}",
            f"SELECT {cols} FROM {source}{where}{order_clause(columns, request.sort)}", params)


def too_many_rows(fmt: str, rows: int, mart_title: str) -> MartViewError:
    limit = EXPORT_LIMITS[fmt]
    others = [f for f, n in EXPORT_LIMITS.items() if f != fmt and n >= rows]
    return MartViewError(409, "MART_EXPORT_TOO_MANY_ROWS",
                         f"{fmt} export: {rows} rows by the current filters, the limit is {limit}",
                         format=fmt, rows=rows, limit=limit, mart=mart_title, other_formats=others)


# ------------------------------------------------------------------ values

def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def excel_value(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    if isinstance(value, time) and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    if value is None or isinstance(value, (bool, int, float, Decimal, str, date, datetime, time)):
        return value
    return str(value)


def file_name(table: str, fmt: str) -> str:
    return f"{table}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt.lower()}"


# ------------------------------------------------------------------ routes

def _detail(action: str, payload: ViewRequest, fmt: str | None = None) -> dict:
    detail: dict[str, Any] = {"action": action,
                              "filters": [f.model_dump() for f in payload.filters],
                              "sort": payload.sort.model_dump() if payload.sort else None}
    if fmt:
        detail["format"] = fmt
    return detail


def _refusal(exc: MartViewError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc), **exc.extra})


@router.get("")
def marts(request: Request) -> list[dict]:
    token_hash = _session_token_hash(request)
    rows = _call(token_hash, "SELECT * FROM app.mart_view_list(%s)", fetch="all")
    status: dict[str, dict] = {}
    try:
        with closing(connect_readonly(config)) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT mart_code, last_run_status, last_run_finished_at, published_at "
                                "FROM etl.v_dm_mart_status WHERE NOT is_deleted")
                    status = {r["mart_code"]: r for r in cur.fetchall()}
            finally:
                conn.rollback()
    except psycopg.Error:
        status = {}
    result = []
    for r in rows:
        s = status.get(r["object_name"], {})
        result.append({"object_id": r["object_id"], "title": r["title"], "description": r["description"],
                       "object_name": r["object_name"],
                       "last_run_status": s.get("last_run_status"),
                       "last_built_at": json_value(s.get("last_run_finished_at")),
                       "published_at": json_value(s.get("published_at"))})
    return result


def _page(conn, schema: str, table: str, payload: ViewRequest, page: int) -> tuple[list[Column], int, list[dict]]:
    with conn.cursor() as cur:
        columns = read_columns(cur, schema, table)
        count_sql, select, params = select_sql(schema, table, columns, payload)
        cur.execute(count_sql, params)
        total = int(cur.fetchone()["n"])
        cur.execute(select + " LIMIT %s OFFSET %s", (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE))
        rows = [{k: json_value(v) for k, v in r.items()} for r in cur.fetchall()]
    return columns, total, rows


def _page_answer(view_id: int, columns: list[Column], total: int, rows: list[dict], page: int,
                 payload: ViewRequest) -> dict:
    return {"view_id": view_id, "page": page, "page_size": PAGE_SIZE, "total": total,
            "columns": [{"name": c.name, "type": c.type, "kind": c.kind} for c in columns],
            "rows": rows, "filters": [f.model_dump() for f in payload.filters],
            "sort": payload.sort.model_dump() if payload.sort else None, "export_limits": EXPORT_LIMITS}


def _user_error(exc: psycopg.Error) -> MartViewError | None:
    if isinstance(exc, (psycopg.errors.DataError, psycopg.errors.InvalidTextRepresentation)):
        return MartViewError(400, "MART_FILTER_VALUE", "a filter value does not fit the type of its column: "
                             + _db_message(exc))
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return MartViewError(400, "MART_QUERY_TIMEOUT", "the query took longer than "
                             f"{STATEMENT_TIMEOUT_MS // 1000} s; narrow the filters")
    return None


@router.post("/{object_id}/view")
def open_view(object_id: int, payload: ViewRequest, request: Request) -> dict:
    token_hash = _session_token_hash(request)
    password = _ai_password()
    begun = _call(token_hash, "SELECT * FROM app.mart_view_begin(%s, %s, %s::jsonb)",
                  (object_id, psycopg.types.json.Jsonb(_detail("VIEW", payload))))
    view_id = int(begun["request_id"])
    if not begun["is_allowed"]:
        raise HTTPException(status_code=403, detail={"code": "AI_OBJECT_NOT_ALLOWED",
                                                     "message": "the mart is not available to the role"})
    try:
        with closing(connect_ai_readonly(config, password)) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
                columns, total, rows = _page(conn, begun["schema_name"], begun["object_name"], payload, 1)
            finally:
                conn.rollback()
    except MartViewError as exc:
        _finish(token_hash, view_id, "REFUSED", exc.code, 0, str(exc))
        raise _refusal(exc) from exc
    except psycopg.Error as exc:
        user = _user_error(exc)
        _finish(token_hash, view_id, "REFUSED" if user else "ERROR", user.code if user else "MART_VIEW_ERROR", 0,
                str(user) if user else _db_message(exc))
        if user:
            raise _refusal(user) from exc
        raise HTTPException(status_code=500, detail=_db_message(exc)) from exc
    _finish(token_hash, view_id, "OK" if total else "NO_DATA", None, total, None)
    return _page_answer(view_id, columns, total, rows, 1, payload)


@router.post("/views/{view_id}/page")
def view_page(view_id: int, payload: PageRequest, request: Request) -> dict:
    token_hash = _session_token_hash(request)
    password = _ai_password()
    cont = _call(token_hash, "SELECT * FROM app.mart_view_continue(%s, %s)", (view_id,))
    if not cont["is_allowed"]:
        raise HTTPException(status_code=403, detail={"code": "AI_OBJECT_NOT_ALLOWED",
                                                     "message": "the mart is not available to the role"})
    # Filters and sort come from the log row of the opening, not from the client.
    detail = cont["view_detail"] or {}
    view = ViewRequest(filters=detail.get("filters") or [], sort=detail.get("sort"))
    try:
        with closing(connect_ai_readonly(config, password)) as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
                columns, total, rows = _page(conn, cont["schema_name"], cont["object_name"], view, payload.page)
            finally:
                conn.rollback()
    except MartViewError as exc:
        raise _refusal(exc) from exc
    except psycopg.Error as exc:
        user = _user_error(exc)
        if user:
            raise _refusal(user) from exc
        raise HTTPException(status_code=500, detail=_db_message(exc)) from exc
    return _page_answer(view_id, columns, total, rows, payload.page, view)


def _csv_stream(conn, select: str, params: list[Any], columns: list[Column], on_end) -> Iterator[bytes]:
    written = 0
    try:
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
        writer.writerow([c.name for c in columns])
        yield ("\ufeff" + buffer.getvalue()).encode("utf-8")
        with conn.cursor(name="mart_export") as cur:
            cur.itersize = FETCH_ROWS
            cur.execute(select, params)
            while True:
                batch = cur.fetchmany(FETCH_ROWS)
                if not batch:
                    break
                buffer = io.StringIO()
                writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
                for row in batch:
                    writer.writerow([text_value(row[c.name]) for c in columns])
                written += len(batch)
                yield buffer.getvalue().encode("utf-8")
        on_end("OK", None, written, None)
    except Exception as exc:  # noqa: BLE001 - the log row must be finished whatever happened
        on_end("ERROR", "MART_EXPORT_ERROR", written, str(exc)[:500])
        raise
    finally:
        conn.rollback()
        conn.close()


def build_xlsx(rows: Iterator[dict], columns: list[Column], title: str, path: str) -> int:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title=(title or "mart")[:31].replace("/", "_").replace("\\", "_").replace(":", "_")
                         .replace("*", "_").replace("?", "_").replace("[", "_").replace("]", "_"))
    header = []
    for c in columns:
        cell = WriteOnlyCell(ws, value=c.name)
        cell.font = Font(bold=True)
        header.append(cell)
    ws.append(header)
    count = 0
    for row in rows:
        ws.append([excel_value(row[c.name]) for c in columns])
        count += 1
    wb.save(path)
    return count


def _register_pdf_font() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "DwhMart" in pdfmetrics.getRegisteredFontNames():
        return "DwhMart", "DwhMartBold"
    pairs = [("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
             (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf")]
    for regular, bold in pairs:
        if os.path.exists(regular) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("DwhMart", regular))
            pdfmetrics.registerFont(TTFont("DwhMartBold", bold))
            return "DwhMart", "DwhMartBold"
    raise RuntimeError("no Unicode font for PDF (fonts-dejavu-core)")


def build_pdf(rows: list[dict], columns: list[Column], title: str, filters_text: str) -> bytes:
    """Landscape A4; wide marts are split into tables of 10 columns, the first column repeated in each."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    regular, bold = _register_pdf_font()
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm, title=title)
    head = ParagraphStyle("h", fontName=bold, fontSize=12, leading=15)
    small = ParagraphStyle("s", fontName=regular, fontSize=8, leading=10)
    story: list[Any] = [Paragraph(title, head),
                        Paragraph(f"{datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(rows)}"
                                  + (f" · {filters_text}" if filters_text else ""), small), Spacer(1, 4 * mm)]
    key, rest = columns[0], columns[1:]
    groups = [rest[i:i + PDF_COLUMNS_PER_TABLE - 1] for i in range(0, len(rest), PDF_COLUMNS_PER_TABLE - 1)] or [[]]
    width = doc.width

    def cut(value: Any) -> str:
        text = text_value(value)
        return text if len(text) <= PDF_CELL_CHARS else text[:PDF_CELL_CHARS - 1] + "…"

    for n, group in enumerate(groups):
        cols = [key, *group]
        if len(groups) > 1:
            story.append(Paragraph(f"{n + 1} / {len(groups)}", small))
        data = [[c.name for c in cols]] + [[cut(r[c.name]) for c in cols] for r in rows]
        table = LongTable(data, repeatRows=1, colWidths=[width / len(cols)] * len(cols))
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), regular), ("FONTNAME", (0, 0), (-1, 0), bold),
            ("FONTSIZE", (0, 0), (-1, -1), 6.5), ("LEADING", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe6ee")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9aa7b5")),
            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(table)
        if n < len(groups) - 1:
            story.append(PageBreak())
    doc.build(story)
    return out.getvalue()


def filters_text(payload: ViewRequest) -> str:
    names = {"eq": "=", "contains": "∋", "gte": "≥", "lte": "≤", "empty": "∅", "not_empty": "≠∅"}
    return "; ".join(f"{f.column} {names[f.op]} {f.value or ''}".strip() for f in payload.filters)


@router.post("/{object_id}/export")
def export(object_id: int, payload: ExportRequest, request: Request):
    token_hash = _session_token_hash(request)
    password = _ai_password()
    fmt = payload.format
    begun = _call(token_hash, "SELECT * FROM app.mart_view_begin(%s, %s, %s::jsonb)",
                  (object_id, psycopg.types.json.Jsonb(_detail("EXPORT", payload, fmt))))
    export_id = int(begun["request_id"])
    if not begun["is_allowed"]:
        raise HTTPException(status_code=403, detail={"code": "AI_OBJECT_NOT_ALLOWED",
                                                     "message": "the mart is not available to the role"})
    schema, table = begun["schema_name"], begun["object_name"]
    title = table
    for item in _call(token_hash, "SELECT * FROM app.mart_view_list(%s)", fetch="all"):
        if int(item["object_id"]) == object_id:
            title = item["title"]
    conn = connect_ai_readonly(config, password)
    handed_over = False
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
            columns = read_columns(cur, schema, table)
            count_sql, select, params = select_sql(schema, table, columns, payload)
            cur.execute(count_sql, params)
            total = int(cur.fetchone()["n"])
        if total > EXPORT_LIMITS[fmt]:
            raise too_many_rows(fmt, total, title)

        def on_end(status: str, code: str | None, rows: int, message: str | None) -> None:
            _finish(token_hash, export_id, status, code, rows, message)

        name = file_name(table, fmt)
        disposition = {"Content-Disposition": f'attachment; filename="{name}"'}
        if fmt == "CSV":
            handed_over = True
            return StreamingResponse(_csv_stream(conn, select, params, columns, on_end),
                                     media_type=MIME["CSV"], headers=disposition)
        with conn.cursor(name="mart_export") as cur:
            cur.itersize = FETCH_ROWS
            cur.execute(select, params)
            if fmt == "PDF":
                rows = cur.fetchall()
                body = build_pdf(rows, columns, f"{title} ({schema}.{table})", filters_text(payload))
                on_end("OK", None, len(rows), None)
                return Response(content=body, media_type=MIME["PDF"], headers=disposition)
            handle = tempfile.NamedTemporaryFile(prefix="dwh_mart_", suffix=".xlsx", delete=False)
            handle.close()
            try:
                count = build_xlsx(iter(cur), columns, title, handle.name)
            except Exception:
                os.unlink(handle.name)
                raise
            on_end("OK", None, count, None)
            return FileResponse(handle.name, media_type=MIME["XLSX"], filename=name,
                                background=BackgroundTask(os.unlink, handle.name))
    except MartViewError as exc:
        _finish(token_hash, export_id, "REFUSED", exc.code, exc.extra.get("rows", 0), str(exc))
        raise _refusal(exc) from exc
    except psycopg.Error as exc:
        user = _user_error(exc)
        _finish(token_hash, export_id, "REFUSED" if user else "ERROR", user.code if user else "MART_EXPORT_ERROR", 0,
                str(user) if user else _db_message(exc))
        if user:
            raise _refusal(user) from exc
        raise HTTPException(status_code=500, detail=_db_message(exc)) from exc
    finally:
        if not handed_over:
            conn.rollback()
            conn.close()
