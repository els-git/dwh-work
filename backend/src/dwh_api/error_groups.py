"""I13-02: the group of an error of a load or a mart, one place for every code of the engines.

The group answers "what kind of problem is it": the connection, the structure, the data format, a field, the key,
a conversion, data quality, the run or the schedule, the settings, a limit, or the system. It is a property of the
code; codes themselves never change (rule 10 of the project). The test backend/tests/test_error_groups.py fails
when an engine raises a code that has no group here.

DQ is reserved: object data quality checks are a separate task (plan I13, decision R3 = A).
"""
from __future__ import annotations

GROUPS: tuple[str, ...] = (
    "CONNECTION", "STRUCTURE", "FORMAT", "FIELD", "KEY", "CONVERSION", "DQ", "SCHEDULE", "CONFIG", "LIMIT",
    "SYSTEM", "OTHER",
)

CODES: dict[str, str] = {}


def _put(group: str, *codes: str) -> None:
    assert group in GROUPS, group
    for code in codes:
        assert code not in CODES, code
        CODES[code] = group


_put("CONNECTION",
     # database sources (source_gateway, run_object)
     "SOURCE_CONNECT_FAILED", "SOURCE_NO_RESPONSE", "SOURCE_ERROR", "LOCK_TIMEOUT", "SOURCE_NOT_READ_ONLY",
     "SOURCE_WRITE_BLOCKED", "CREDENTIAL_UNAVAILABLE",
     # API sources (http_gateway, api_request); HTTP_<status> is matched by prefix below
     "API_UNREACHABLE", "API_TIMEOUT", "API_TLS", "API_HOST_NOT_ALLOWED", "API_PLAIN_HTTP", "API_REDIRECTED",
     "API_CREDENTIAL_MISSING", "API_NEXT_LINK_HOST",
     # file sources (file_gateway)
     "FILE_FOLDER_MISSING", "FILE_FOLDER_INVALID", "FILE_FOLDER_UNREADABLE",
     # SQL marts
     "DM_SQL_CONNECT_FAILED")
_put("STRUCTURE",
     "SCHEMA_DRIFT", "TARGET_DRIFT", "SOURCE_DRIFT", "SOURCE_OBJECT_MISSING", "SOURCE_COLUMN_MISSING",
     "SOURCE_TYPE_CHANGED", "APPLY_MISMATCH", "DM_TABLE_MISMATCH", "DM_SQL_COLUMNS_CHANGED", "DM_SOURCE_MISSING")
_put("FORMAT",
     "FILE_BROKEN", "FILE_XML_DTD_FORBIDDEN", "FILE_TOO_LARGE", "FILE_TOO_MANY", "FILE_UNREADABLE",
     "FILE_BAD_RECORDS", "FILE_NO_DATE", "API_BAD_JSON", "API_BAD_XML", "API_BAD_RECORDS", "API_RESPONSE_TOO_LARGE",
     "API_TOO_MANY_PAGES")
_put("FIELD",
     "TARGET_NOT_NULL", "TARGET_CHECK", "TARGET_REFERENCE")
_put("KEY",
     "KEY_NULL", "KEY_DUPLICATE", "TARGET_UNIQUE", "DM_KEY_NULL", "DM_KEY_NOT_UNIQUE", "DM_LOOKUP_NOT_UNIQUE")
_put("CONVERSION",
     "TYPE_CONVERSION", "NUMERIC_OUT_OF_RANGE", "VALUE_TOO_LONG", "DM_DATA_ERROR")
_put("SCHEDULE",
     "DISPATCHER_LOST", "SKIPPED_BUSY", "SKIPPED_NOT_READY", "RUN_REFUSED", "ODS_REFUSED", "UNSUPPORTED_RUN_KIND")
_put("CONFIG",
     "CONFIG_INVALID", "NOT_CONFIGURED", "ODS_NOT_CONFIGURED", "API_CONFIG_INVALID", "API_CONFIG_SECRET",
     "API_URL_INVALID", "CURL_INVALID", "CURL_METHOD", "CURL_UNSUPPORTED", "FILE_CONFIG_INVALID",
     "DM_DEFINITION_INVALID", "DM_SQL_NOT_ALLOWED", "DM_SQL_NOT_CONFIGURED")
_put("LIMIT",
     "ROW_LIMIT", "DISK_SPACE", "TIMEOUT", "DM_TIMEOUT")
_put("SYSTEM",
     "INTERNAL_ERROR", "DATABASE_ERROR", "DM_DATABASE_ERROR", "DM_FAILED", "RUN_FAILED")


def group_of(code: str | None) -> str:
    """The group of an error code; OTHER for an unknown or empty code (old records of the removed contour)."""
    value = (code or "").strip().upper()
    if not value:
        return "OTHER"
    if value.startswith("HTTP_"):
        return "CONNECTION"
    return CODES.get(value, "OTHER")


def with_groups(rows: list[dict]) -> list[dict]:
    """Adds error_group to rows that have error_code (the history API)."""
    for row in rows:
        row["error_group"] = group_of(row.get("error_code"))
    return rows
