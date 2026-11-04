"""Normalized field types and type compatibility (I05-01, decision M6).

Every source/target column type is reduced to a small vocabulary shared by all
connectors (PostgreSQL now; Oracle, JSON and XML later map into the same names):

  TEXT, TEXT(n), INTEGER, BIGINT, NUMERIC, NUMERIC(p,s), FLOAT, BOOLEAN,
  DATE, TIME, TIMESTAMP, TIMESTAMPTZ, UUID, JSON, BYTES, OTHER

compatibility(source, target) returns:
  SAME         identical types, no conversion
  SAFE         lossless conversion (widening)
  CONDITIONAL  may fail or lose data for some values; needs explicit confirmation
  INCOMPATIBLE not allowed
"""

from __future__ import annotations

import re

SAME, SAFE, CONDITIONAL, INCOMPATIBLE = "SAME", "SAFE", "CONDITIONAL", "INCOMPATIBLE"

_TYPE_RE = re.compile(r"^([A-Z]+)(?:\((\d{1,4})(?:,(\d{1,4}))?\))?$")
_PG_SIMPLE = {
    "text": "TEXT", "smallint": "INTEGER", "integer": "INTEGER", "bigint": "BIGINT",
    "real": "FLOAT", "double precision": "FLOAT", "boolean": "BOOLEAN", "date": "DATE",
    "time without time zone": "TIME", "time with time zone": "TIME",
    "timestamp without time zone": "TIMESTAMP", "timestamp with time zone": "TIMESTAMPTZ",
    "uuid": "UUID", "json": "JSON", "jsonb": "JSON", "bytea": "BYTES", "numeric": "NUMERIC",
    "name": "TEXT", "citext": "TEXT", "time": "TIME", "timestamp": "TIMESTAMP", "timestamptz": "TIMESTAMPTZ",
}
_PG_TARGET = {
    "TEXT": "text", "INTEGER": "integer", "BIGINT": "bigint", "NUMERIC": "numeric", "FLOAT": "double precision",
    "BOOLEAN": "boolean", "DATE": "date", "TIME": "time", "TIMESTAMP": "timestamp", "TIMESTAMPTZ": "timestamptz",
    "UUID": "uuid", "JSON": "jsonb", "BYTES": "bytea",
}
_INTEGER_DIGITS = {"INTEGER": 10, "BIGINT": 19}


def parse(type_name: str) -> tuple[str, int | None, int | None]:
    m = _TYPE_RE.fullmatch(type_name or "")
    if not m:
        raise ValueError("Invalid normalized type")
    base, a, b = m.group(1), m.group(2), m.group(3)
    return base, int(a) if a is not None else None, int(b) if b is not None else None


def normalize_postgres(formatted_type: str) -> str:
    """Normalized name of a PostgreSQL type as printed by format_type()."""
    t = (formatted_type or "").strip().lower()
    if t.endswith("[]"):
        return "OTHER"
    if t in _PG_SIMPLE:
        return _PG_SIMPLE[t]
    m = re.fullmatch(r"(character varying|varchar|character|char|bpchar)\((\d+)\)", t)
    if m:
        return f"TEXT({m.group(2)})"
    if t in ("character varying", "varchar"):
        return "TEXT"
    if t in ("character", "char", "bpchar"):
        return "TEXT(1)"
    m = re.fullmatch(r"numeric\((\d+)(?:,(\d+))?\)", t)
    if m:
        return f"NUMERIC({m.group(1)},{m.group(2) or 0})"
    m = re.fullmatch(r"(timestamp|time)\(\d\) (without|with) time zone", t)
    if m:
        return {("timestamp", "without"): "TIMESTAMP", ("timestamp", "with"): "TIMESTAMPTZ"}.get(
            (m.group(1), m.group(2)), "TIME")
    return "OTHER"


def postgres_type(type_name: str) -> str:
    """PostgreSQL column type for a normalized type (used in DDL proposals)."""
    base, a, b = parse(type_name)
    if base == "TEXT" and a is not None:
        return f"varchar({a})"
    if base == "NUMERIC" and a is not None:
        return f"numeric({a},{b or 0})"
    if base not in _PG_TARGET:
        raise ValueError("Type has no PostgreSQL column equivalent")
    return _PG_TARGET[base]


def _integer_digits(base: str, a: int | None, b: int | None) -> int | None:
    if base in _INTEGER_DIGITS:
        return _INTEGER_DIGITS[base]
    if base == "NUMERIC" and a is not None:
        return a - (b or 0)
    return None


def compatibility(source: str, target: str) -> str:
    sb, sa, ss = parse(source)
    tb, ta, ts = parse(target)
    if (sb, sa, ss) == (tb, ta, ts):
        return SAME if sb != "OTHER" else CONDITIONAL
    if "OTHER" in (sb, tb):
        return CONDITIONAL if tb == "TEXT" and ta is None else INCOMPATIBLE
    numeric = ("INTEGER", "BIGINT", "NUMERIC", "FLOAT")
    if tb == "TEXT":
        if sb == "BYTES":
            return CONDITIONAL
        if ta is None:
            return SAFE
        return SAFE if sb == "TEXT" and sa is not None and sa <= ta else CONDITIONAL
    if sb == "TEXT":
        return CONDITIONAL
    if sb in numeric and tb in numeric:
        if tb == "FLOAT":
            return SAFE if sb == "INTEGER" else CONDITIONAL
        if sb == "FLOAT":
            return CONDITIONAL
        if tb == "NUMERIC" and ta is None:
            return SAFE
        src_digits, dst_digits = _integer_digits(sb, sa, ss), _integer_digits(tb, ta, ts)
        src_scale, dst_scale = (ss or 0) if sb == "NUMERIC" else 0, (ts or 0) if tb == "NUMERIC" else 0
        if sb == "NUMERIC" and sa is None:
            return CONDITIONAL
        if src_digits is not None and dst_digits is not None and src_digits <= dst_digits and src_scale <= dst_scale:
            return SAFE
        return CONDITIONAL
    temporal = {("DATE", "TIMESTAMP"): SAFE, ("DATE", "TIMESTAMPTZ"): SAFE,
                ("TIMESTAMP", "TIMESTAMPTZ"): CONDITIONAL, ("TIMESTAMPTZ", "TIMESTAMP"): CONDITIONAL,
                ("TIMESTAMP", "DATE"): CONDITIONAL, ("TIMESTAMPTZ", "DATE"): CONDITIONAL}
    if (sb, tb) in temporal:
        return temporal[(sb, tb)]
    if (sb, tb) == ("BOOLEAN", "INTEGER"):
        return CONDITIONAL
    return INCOMPATIBLE


def conversion_for(compat: str, confirmed: bool) -> str | None:
    """Value stored in object_config_field.conversion, or None when not allowed."""
    if compat == SAME:
        return "NONE"
    if compat == SAFE:
        return "SAFE"
    if compat == CONDITIONAL and confirmed:
        return "CONFIRMED"
    return None
