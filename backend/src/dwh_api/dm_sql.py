"""SQL of a DM mart from its version (I19 step 5; package 018, decisions D1-D12).

Pure functions: no connection is opened here. The engine (etl/src/dm_mart_worker.py, role dwh_etl) executes
the statements; the API shows the same text as a preview (render()).

Shape of the fill statement (D1: every source is reduced to one row per key before the join, so sums are
not multiplied):

  INSERT INTO "dm"."<code>" (<fields>, "dm_loaded_at")
  SELECT <field expressions>, now()
    FROM (SELECT * FROM "ods"."<grain>" WHERE <filters>) AS "<g>"
    LEFT JOIN (SELECT * FROM "ods"."<t>" WHERE ...) AS "<s>" ON "<s>"."<key>" = "<g>"."<grain key>"    SAME_KEY
    LEFT JOIN (SELECT "<key>" AS "__key", SUM("<c>") AS "__a1", ... FROM "ods"."<t>" WHERE ...
               GROUP BY "<key>") AS "<c>" ON "<c>"."__key" = "<g>"."<grain key>"                         CHILD
    LEFT JOIN (SELECT * FROM "ods"."<t>" WHERE ...) AS "<l>" ON "<l>"."<lookup col>" = "<ref>"."<ref col>"  LOOKUP

A field with several sources takes the first non-empty value (D3): COALESCE in priority order; COUNT and
COUNT_DISTINCT of a key without child rows give 0. varchar(n) fields are built as text, so a value that is too
long fails the INSERT instead of being cut. Filter values are parameters cast to the type of their column.
Uniqueness of the GRAIN / SAME_KEY keys and of the LOOKUP columns is checked before the fill (key_checks()).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

AGGREGATES = {"SUM": "sum({})", "COUNT": "count({})", "COUNT_DISTINCT": "count(DISTINCT {})", "MIN": "min({})",
              "MAX": "max({})", "AVG": "avg({})"}
OPERATORS = ("=", "<>", ">", ">=", "<", "<=", "IN", "IS NULL", "IS NOT NULL", "LIKE")
_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_TYPE = re.compile(r"^(text|bigint|integer|smallint|numeric|numeric\(\d{1,2},\d{1,2}\)|date|timestamp|timestamptz|boolean"
                   r"|varchar\(\d{1,4}\))$")
KEY_EXAMPLES = 10


class DefinitionError(ValueError):
    """The stored version cannot be turned into SQL (the database normally refuses it earlier)."""


def qi(name: str) -> str:
    if not _NAME.match(name or ""):
        raise DefinitionError(f"invalid identifier {name!r}")
    return '"' + name + '"'


@dataclass
class Source:
    alias: str
    table: str
    kind: str
    key_column: str
    ref_alias: str | None = None
    ref_column: str | None = None


@dataclass
class FieldSource:
    alias: str
    column: str
    aggregate: str = "NONE"


@dataclass
class Field:
    name: str
    data_type: str
    is_key: bool
    sources: list[FieldSource]
    title: str | None = None


@dataclass
class Filter:
    alias: str
    column: str
    operator: str
    value: Any = None


@dataclass
class Definition:
    mart_code: str
    version_id: int
    version_no: int
    sources: list[Source]
    fields: list[Field]
    filters: list[Filter] = field(default_factory=list)

    def source(self, alias: str) -> Source:
        for s in self.sources:
            if s.alias == alias:
                return s
        raise DefinitionError(f"unknown source {alias}")

    @property
    def grain(self) -> Source:
        grains = [s for s in self.sources if s.kind == "GRAIN"]
        if len(grains) != 1:
            raise DefinitionError("exactly one GRAIN source is required")
        return grains[0]

    def tables(self) -> set[str]:
        return {s.table for s in self.sources}


@dataclass
class KeyCheck:
    """A query returning (key_value text, rows bigint, duplicate_keys bigint) for duplicated keys of a source."""
    code: str
    alias: str
    table: str
    column: str
    sql: str
    params: list[Any]


# ------------------------------------------------------------------ loading the version

def _rows(cur) -> list[dict[str, Any]]:
    rows = cur.fetchall()
    if rows and isinstance(rows[0], dict):
        return [dict(r) for r in rows]
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in rows]


def load_definition(cur, version_id: int) -> Definition:
    """Reads a version from meta.dm_* (dwh_app and dwh_etl have SELECT)."""
    cur.execute("SELECT v.version_id, v.version_no, m.mart_code FROM meta.dm_mart_version v "
                "JOIN meta.dm_mart m ON m.mart_id = v.mart_id WHERE v.version_id = %s", (version_id,))
    head = _rows(cur)
    if not head:
        raise DefinitionError(f"mart version {version_id} not found")
    cur.execute("SELECT alias, table_name, source_kind, key_column, ref_alias, ref_column FROM meta.dm_mart_source "
                "WHERE version_id = %s ORDER BY position", (version_id,))
    sources = [Source(r["alias"], r["table_name"], r["source_kind"], r["key_column"], r["ref_alias"], r["ref_column"])
               for r in _rows(cur)]
    cur.execute("SELECT f.field_id, f.field_name, f.title, f.data_type, f.is_key, s.source_alias, s.source_column, "
                "s.aggregate_fn FROM meta.dm_mart_field f LEFT JOIN meta.dm_mart_field_source s ON s.field_id = f.field_id "
                "WHERE f.version_id = %s ORDER BY f.position, s.priority_no", (version_id,))
    fields: dict[int, Field] = {}
    for r in _rows(cur):
        f = fields.setdefault(r["field_id"], Field(r["field_name"], r["data_type"], bool(r["is_key"]), [], r["title"]))
        if r["source_alias"] is not None:
            f.sources.append(FieldSource(r["source_alias"], r["source_column"], r["aggregate_fn"]))
    cur.execute("SELECT source_alias, column_name, operator, value FROM meta.dm_mart_filter WHERE version_id = %s "
                "ORDER BY position", (version_id,))
    filters = [Filter(r["source_alias"], r["column_name"], r["operator"],
                      json.loads(r["value"]) if isinstance(r["value"], str) else r["value"]) for r in _rows(cur)]
    return Definition(head[0]["mart_code"], int(head[0]["version_id"]), int(head[0]["version_no"]), sources,
                      list(fields.values()), filters)


def load_column_types(cur, tables: set[str]) -> dict[tuple[str, str], str]:
    """format_type() of every column of the ODS tables (catalog only; readable by every role)."""
    if not tables:
        return {}
    cur.execute("SELECT c.relname AS table_name, a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS typ "
                "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'ods' AND c.relname = ANY(%s) AND a.attnum > 0 AND NOT a.attisdropped",
                (sorted(tables),))
    return {(r["table_name"], r["column_name"]): r["typ"] for r in _rows(cur)}


def missing_columns(defn: Definition, types: dict[tuple[str, str], str]) -> list[str]:
    """Tables and columns the version uses that the ODS no longer has."""
    used: list[tuple[str, str]] = []
    for s in defn.sources:
        used.append((s.table, s.key_column))
        if s.kind == "LOOKUP":
            used.append((defn.source(s.ref_alias).table, s.ref_column))
    for f in defn.fields:
        used += [(defn.source(x.alias).table, x.column) for x in f.sources]
    used += [(defn.source(x.alias).table, x.column) for x in defn.filters]
    tables = {t for t, _ in types}
    out: list[str] = []
    for table, column in used:
        text = f"ods.{table}" if table not in tables else f"ods.{table}.{column}"
        if (table, column) not in types and text not in out:
            out.append(text)
    return out


# ------------------------------------------------------------------ statements

def _filters(defn: Definition, alias: str, types: dict[tuple[str, str], str], params: list[Any]) -> str:
    table = defn.source(alias).table
    parts = []
    for f in defn.filters:
        if f.alias != alias:
            continue
        col = qi(f.column)
        typ = types.get((table, f.column))
        if f.operator not in OPERATORS:
            raise DefinitionError(f"unsupported operator {f.operator}")
        if f.operator in ("IS NULL", "IS NOT NULL"):
            parts.append(f"{col} {f.operator}")
            continue
        if typ is None:
            raise DefinitionError(f"column ods.{table}.{f.column} not found")
        if f.operator == "LIKE":
            parts.append(f"{col}::text LIKE %s")
            params.append(str(f.value))
        elif f.operator == "IN":
            if not isinstance(f.value, list) or not f.value:
                raise DefinitionError("IN needs a list of values")
            parts.append(f"{col} = ANY(CAST(%s AS {typ}[]))")
            params.append([_text(v) for v in f.value])
        else:
            parts.append(f"{col} {f.operator} CAST(%s AS {typ})")
            params.append(_text(f.value))
    return " AND ".join(parts)


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _subquery(defn: Definition, s: Source, types, params: list[Any]) -> str:
    where = _filters(defn, s.alias, types, params)
    return f"(SELECT * FROM \"ods\".{qi(s.table)}" + (f" WHERE {where}" if where else "") + ")"


def _cast_type(data_type: str) -> str:
    if not _TYPE.match(data_type):
        raise DefinitionError(f"unsupported field type {data_type}")
    return "text" if data_type.startswith("varchar") else data_type


def fill_sql(defn: Definition, types: dict[tuple[str, str], str], target: str | None = None) -> tuple[str, list[Any]]:
    """INSERT ... SELECT of the whole mart and its parameters (the DELETE is done separately).

    target: the table to fill, by default dm.<mart code>; a publication with a new structure fills
    dm.<mart code>__next, which the database then swaps in (package 019)."""
    grain = defn.grain
    key_fields = [f for f in defn.fields if f.is_key]
    if len(key_fields) != 1:
        raise DefinitionError("exactly one key field is required")
    params: list[Any] = []
    joins: list[str] = [f"  FROM {_subquery(defn, grain, types, params)} AS {qi(grain.alias)}"]
    child_cols: dict[tuple[str, str, str], str] = {}
    for f in defn.fields:
        for x in f.sources:
            if defn.source(x.alias).kind == "CHILD":
                if x.aggregate not in AGGREGATES:
                    raise DefinitionError(f"column {x.alias}.{x.column} of a CHILD source needs an aggregate")
                child_cols.setdefault((x.alias, x.column, x.aggregate), f"__a{len(child_cols) + 1}")
            elif x.aggregate != "NONE":
                raise DefinitionError(f"aggregate is allowed only for CHILD sources ({x.alias}.{x.column})")
    for s in defn.sources:
        if s is grain:
            continue
        on_grain = f"{qi(s.alias)}.{{}} = {qi(grain.alias)}.{qi(grain.key_column)}"
        if s.kind == "SAME_KEY":
            joins.append(f"  LEFT JOIN {_subquery(defn, s, types, params)} AS {qi(s.alias)} "
                         f"ON {on_grain.format(qi(s.key_column))}")
        elif s.kind == "CHILD":
            aggs = [f"{AGGREGATES[agg].format(qi(col))} AS {qi(name)}"
                    for (alias, col, agg), name in child_cols.items() if alias == s.alias]
            where = _filters(defn, s.alias, types, params)
            joins.append(f"  LEFT JOIN (SELECT {qi(s.key_column)} AS \"__key\""
                         + "".join(f", {a}" for a in aggs)
                         + f" FROM \"ods\".{qi(s.table)}" + (f" WHERE {where}" if where else "")
                         + f" GROUP BY {qi(s.key_column)}) AS {qi(s.alias)} ON {on_grain.format(qi('__key'))}")
        elif s.kind == "LOOKUP":
            ref = defn.source(s.ref_alias)
            if ref.kind == "CHILD":
                raise DefinitionError(f"a lookup cannot refer to the CHILD source {ref.alias}")
            joins.append(f"  LEFT JOIN {_subquery(defn, s, types, params)} AS {qi(s.alias)} "
                         f"ON {qi(s.alias)}.{qi(s.key_column)} = {qi(ref.alias)}.{qi(s.ref_column)}")
        else:
            raise DefinitionError(f"unsupported source kind {s.kind}")
    exprs: list[str] = []
    for f in defn.fields:
        if not f.sources:
            raise DefinitionError(f"field {f.name} has no source")
        cast = _cast_type(f.data_type)
        parts = []
        for x in f.sources:
            if defn.source(x.alias).kind == "CHILD":
                value = f"{qi(x.alias)}.{qi(child_cols[(x.alias, x.column, x.aggregate)])}"
                if x.aggregate in ("COUNT", "COUNT_DISTINCT"):
                    value = f"coalesce({value}, 0)"
            else:
                value = f"{qi(x.alias)}.{qi(x.column)}"
            parts.append(f"CAST({value} AS {cast})")
        expr = parts[0] if len(parts) == 1 else "coalesce(" + ", ".join(parts) + ")"
        exprs.append(f"  {expr} AS {qi(f.name)}")
    columns = ", ".join(qi(f.name) for f in defn.fields)
    text = (f"INSERT INTO \"dm\".{qi(target or defn.mart_code)} ({columns}, \"dm_loaded_at\")\nSELECT\n"
            + ",\n".join(exprs) + ",\n  now()\n" + "\n".join(joins))
    # Filters of the grain are inside its subquery; parameters follow the order of the text.
    return text, params


def key_checks(defn: Definition, types: dict[tuple[str, str], str]) -> list[KeyCheck]:
    """Duplicated keys make the joins multiply rows: GRAIN and SAME_KEY keys, LOOKUP columns must be unique."""
    checks: list[KeyCheck] = []
    for s in defn.sources:
        if s.kind not in ("GRAIN", "SAME_KEY", "LOOKUP"):
            continue
        params: list[Any] = []
        sub = _subquery(defn, s, types, params)
        col = qi(s.key_column)
        text = (f"SELECT {col}::text AS key_value, count(*) AS rows, count(*) OVER () AS duplicate_keys "
                f"FROM {sub} AS x WHERE {col} IS NOT NULL GROUP BY {col} HAVING count(*) > 1 "
                f"ORDER BY count(*) DESC, 1 LIMIT {KEY_EXAMPLES}")
        checks.append(KeyCheck("DM_LOOKUP_NOT_UNIQUE" if s.kind == "LOOKUP" else "DM_KEY_NOT_UNIQUE",
                               s.alias, s.table, s.key_column, text, params))
    return checks


def null_key_check(defn: Definition, types: dict[tuple[str, str], str]) -> tuple[str, list[Any]]:
    params: list[Any] = []
    g = defn.grain
    return (f"SELECT count(*) FROM {_subquery(defn, g, types, params)} AS x WHERE {qi(g.key_column)} IS NULL", params)


def render(text: str, params: list[Any]) -> str:
    """Text with the parameters inlined, for display only (never executed)."""
    def literal(value: Any) -> str:
        if isinstance(value, list):
            return "ARRAY[" + ", ".join(literal(v) for v in value) + "]"
        return "'" + str(value).replace("'", "''") + "'"
    out, i = [], 0
    for piece in re.split(r"(%s)", text):
        if piece == "%s":
            out.append(literal(params[i]))
            i += 1
        else:
            out.append(piece)
    return "".join(out)
