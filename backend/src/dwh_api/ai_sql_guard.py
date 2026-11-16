"""Check of the SQL the model wrote for a report (I17 step 5, I17-05, I17-08). Fail-closed: anything unclear is refused.

Two independent checks, both must pass before the query runs (as dwh_ai_ro, read-only, statement timeout):
1. text: one statement, SELECT or WITH, no comments, no forbidden words (writes, DDL, transaction and session
   commands, server functions such as pg_*, dblink, COPY); every reference to a relation of schema dm must be an
   object of the whitelist allowed to the user; no other schema may be named;
2. plan: EXPLAIN (FORMAT JSON, VERBOSE) of the query (VERBOSE gives the schema of every relation); every relation in the plan must belong to the allowed objects or to
   the relations they are built on (a view shows its tables in the plan). The closure is computed by the caller
   from pg_depend / pg_rewrite (allowed_closure()).
The result is wrapped as SELECT * FROM (...) LIMIT n+1, so at most n rows are returned and truncation is visible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

MAX_SQL_CHARS = 20000
STRING_RE = re.compile(r"'(?:[^']|'')*'")
QUOTED_IDENT_RE = re.compile(r'"(?:[^"]|"")*"')
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
QUALIFIED_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\b")
FORBIDDEN_WORDS = {
    "insert", "update", "delete", "merge", "truncate", "create", "alter", "drop", "grant", "revoke", "comment",
    "copy", "call", "do", "execute", "prepare", "deallocate", "listen", "notify", "unlisten", "lock", "vacuum",
    "analyze", "cluster", "reindex", "refresh", "set", "reset", "begin", "commit", "rollback", "savepoint",
    "release", "start", "discard", "load", "security", "into", "dblink", "current_setting", "set_config",
    "query_to_xml", "table_to_xml", "cursor_to_xml", "lo_import", "lo_export", "lo_get", "txid_current",
}
FORBIDDEN_PREFIXES = ("pg_", "lo_", "dblink")


class SqlRejected(ValueError):
    """The query is refused; code is the reason for the journal (result_code)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AllowedObject:
    object_id: int
    schema_name: str
    object_name: str


def extract_sql(answer: str) -> str:
    """The SQL from a model answer: the first ```sql block, else the whole answer."""
    found = re.search(r"```(?:sql)?\s*(.*?)```", answer, re.S | re.I)
    return (found.group(1) if found else answer).strip()


def _strip_literals(sql: str) -> str:
    return STRING_RE.sub("''", sql)


def check_text(sql: str, allowed: Iterable[AllowedObject], dm_relations: Iterable[str]) -> tuple[str, list[int]]:
    """Return the normalized statement and the ids of the whitelist objects it uses, or raise SqlRejected."""
    statement = (sql or "").strip().rstrip(";").strip()
    if not statement:
        raise SqlRejected("AI_SQL_EMPTY", "no query")
    if statement.upper().startswith("-- NO_DATA"):
        raise SqlRejected("AI_NO_DATA", "the model found no data for the description")
    if statement.upper().startswith("-- DENIED"):
        raise SqlRejected("AI_OBJECT_NOT_ALLOWED", "the data is not available to the user's role")
    if len(statement) > MAX_SQL_CHARS:
        raise SqlRejected("AI_SQL_TOO_LONG", "the query is too long")
    if QUOTED_IDENT_RE.search(_strip_literals(statement)):
        raise SqlRejected("AI_SQL_QUOTED_NAME", "quoted names are not allowed")
    bare = _strip_literals(statement)
    if ";" in bare:
        raise SqlRejected("AI_SQL_MULTIPLE", "only one statement is allowed")
    if "--" in bare or "/*" in bare:
        raise SqlRejected("AI_SQL_COMMENT", "comments are not allowed")
    if "$" in bare:
        raise SqlRejected("AI_SQL_DOLLAR", "dollar quoting and parameters are not allowed")
    words = [w.lower() for w in WORD_RE.findall(bare)]
    if not words or words[0] not in ("select", "with"):
        raise SqlRejected("AI_SQL_NOT_SELECT", "only SELECT is allowed")
    for word in words:
        if word in FORBIDDEN_WORDS or word.startswith(FORBIDDEN_PREFIXES):
            raise SqlRejected("AI_SQL_FORBIDDEN", f"forbidden word: {word}")
    allowed_by_name = {o.object_name.lower(): o for o in allowed if o.schema_name == "dm"}
    dm_names = {name.lower() for name in dm_relations}
    used: set[int] = set()
    for schema, name in QUALIFIED_RE.findall(bare):
        schema = schema.lower()
        if schema == "dm":
            obj = allowed_by_name.get(name.lower())
            if obj is None:
                raise SqlRejected("AI_OBJECT_NOT_ALLOWED", f"dm.{name} is not available to the user's role")
            used.add(obj.object_id)
        elif schema in ("pg_catalog", "information_schema", "app", "meta", "etl", "stg", "ods", "audit", "dds",
                        "dq", "secret_store", "public"):
            raise SqlRejected("AI_OBJECT_NOT_ALLOWED", f"schema {schema} is not allowed")
    for word in set(words):
        if word in dm_names:
            obj = allowed_by_name.get(word)
            if obj is None:
                raise SqlRejected("AI_OBJECT_NOT_ALLOWED", f"dm.{word} is not available to the user's role")
            used.add(obj.object_id)
    if not used:
        raise SqlRejected("AI_SQL_NO_OBJECT", "the query uses no reporting object")
    return statement, sorted(used)


def plan_relations(plan: Any) -> set[tuple[str, str]]:
    """(schema, relation) of every scan in an EXPLAIN (FORMAT JSON) result."""
    found: set[tuple[str, str]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "Relation Name" in node:
                # Without a schema the relation cannot be placed: it stays outside every closure (refused).
                found.add((str(node.get("Schema", "")), str(node["Relation Name"])))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(plan)
    return found


def check_plan(plan: Any, closure: set[tuple[str, str]]) -> None:
    outside = sorted(f"{s}.{r}" for s, r in plan_relations(plan) if (s, r) not in closure)
    if outside:
        raise SqlRejected("AI_OBJECT_NOT_ALLOWED", "the query reads relations outside the allowed objects: "
                          + ", ".join(outside))


def limited(statement: str, max_rows: int) -> str:
    return f"SELECT * FROM (\n{statement}\n) AS ai_report LIMIT {int(max_rows) + 1}"


CLOSURE_SQL = """
WITH RECURSIVE dep(oid) AS (
  SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE (n.nspname, c.relname) IN (SELECT * FROM unnest(%s::text[], %s::text[]))
  UNION
  SELECT d.refobjid FROM dep
    JOIN pg_rewrite rw ON rw.ev_class = dep.oid
    JOIN pg_depend d ON d.classid = 'pg_rewrite'::regclass AND d.objid = rw.oid
                    AND d.refclassid = 'pg_class'::regclass AND d.refobjid <> dep.oid
)
SELECT n.nspname AS schema_name, c.relname AS relation_name
  FROM dep JOIN pg_class c ON c.oid = dep.oid JOIN pg_namespace n ON n.oid = c.relnamespace
"""


def allowed_closure(cur, allowed: Iterable[AllowedObject]) -> set[tuple[str, str]]:
    objects = list(allowed)
    cur.execute(CLOSURE_SQL, ([o.schema_name for o in objects], [o.object_name for o in objects]))
    rows = cur.fetchall()
    return {(r["schema_name"], r["relation_name"]) if isinstance(r, dict) else (r[0], r[1]) for r in rows}
