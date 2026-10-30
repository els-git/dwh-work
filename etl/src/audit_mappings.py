from __future__ import annotations

import json
import re
import runpy
from collections import Counter
from pathlib import Path

from dwh.config import get_dwh_config
from dwh.db import connect


PROJECT_ROOT = Path(__file__).resolve().parents[2]

TRANSFORM_PATH = (
    PROJECT_ROOT
    / "etl"
    / "src"
    / "transform_ods.py"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "runtime"
    / "temp"
    / "integration"
    / "mapping_audit"
)

OUTPUT_JSON = OUTPUT_DIR / "mapping_audit.json"
OUTPUT_TXT = OUTPUT_DIR / "mapping_audit.txt"


# Exact current Retail source-object mapping.
# Do not infer "user" -> "users" automatically:
# keep source metadata explicit.
SOURCE_OBJECT_MAP = {
    "crm_retail_application": (
        "app_retail",
        "application",
    ),
    "crm_retail_applicant": (
        "app_retail",
        "applicant",
    ),
    "crm_retail_credit_info": (
        "app_retail",
        "credit_info",
    ),
    "crm_retail_workflow_history": (
        "app_retail",
        "workflow_history",
    ),
    "crm_retail_product": (
        "app_retail",
        "product",
    ),
    "crm_retail_stage": (
        "app_retail",
        "stage",
    ),
    "crm_retail_status": (
        "app_retail",
        "status",
    ),
    "crm_retail_currency": (
        "app_retail",
        "dir_currency",
    ),
    "crm_retail_sales_channel": (
        "app_retail",
        "dir_sales_channel",
    ),
    "crm_retail_branch": (
        "app_retail",
        "dir_branch",
    ),
    "crm_retail_user": (
        "app_retail",
        "users",
    ),
    "crm_retail_integration_log": (
        "integration_retail",
        "integration_log",
    ),
}


SOURCE_DATA_RE = re.compile(
    r"""
    (?:
        \b[a-zA-Z_][a-zA-Z0-9_]*\.
    )?
    source_data
    \s*->>\s*
    '([^']+)'
    """,
    re.IGNORECASE | re.VERBOSE,
)


STG_TECH_RE = re.compile(
    r"""
    (?:
        \b[a-zA-Z_][a-zA-Z0-9_]*\.
    )?
    (
        _dwh_[a-zA-Z0-9_]+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def split_top_level(value: str) -> list[str]:
    """
    Split SQL comma-list without splitting commas inside:
      (...)
      'string literals'
      "quoted identifiers"
    """

    result: list[str] = []
    buffer: list[str] = []

    depth = 0
    in_single = False
    in_double = False

    i = 0

    while i < len(value):
        ch = value[i]

        if in_single:
            buffer.append(ch)

            if ch == "'":
                if (
                    i + 1 < len(value)
                    and value[i + 1] == "'"
                ):
                    buffer.append(
                        value[i + 1]
                    )
                    i += 1
                else:
                    in_single = False

            i += 1
            continue

        if in_double:
            buffer.append(ch)

            if ch == '"':
                if (
                    i + 1 < len(value)
                    and value[i + 1] == '"'
                ):
                    buffer.append(
                        value[i + 1]
                    )
                    i += 1
                else:
                    in_double = False

            i += 1
            continue

        if ch == "'":
            in_single = True
            buffer.append(ch)

        elif ch == '"':
            in_double = True
            buffer.append(ch)

        elif ch == "(":
            depth += 1
            buffer.append(ch)

        elif ch == ")":
            depth -= 1

            if depth < 0:
                raise RuntimeError(
                    "Unbalanced SQL parentheses"
                )

            buffer.append(ch)

        elif ch == "," and depth == 0:
            item = "".join(buffer).strip()

            if item:
                result.append(item)

            buffer = []

        else:
            buffer.append(ch)

        i += 1

    if in_single or in_double:
        raise RuntimeError(
            "Unterminated SQL quote"
        )

    if depth != 0:
        raise RuntimeError(
            "Unbalanced SQL parentheses"
        )

    item = "".join(buffer).strip()

    if item:
        result.append(item)

    return result


def normalize_sql(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def parse_upsert_sql(
    *,
    transform_name: str,
    stg_table: str,
    expected_ods_table: str,
    sql: str,
) -> tuple[
    str,
    list[str],
    list[str],
]:
    """
    Parse only the INSERT target-column list and SELECT list.

    Current ETL contract:
        INSERT INTO ods.table (...)
        SELECT ...
        FROM stg.table

    ON CONFLICT section is outside the parsed SELECT.
    """

    pattern = re.compile(
        r"""
        INSERT\s+INTO\s+
        ods\.
        (?P<ods_table>
            [a-zA-Z_][a-zA-Z0-9_]*
        )
        \s*
        \(
            (?P<target_columns>.*?)
        \)
        \s*
        SELECT
            (?P<select_list>.*?)
        \s+
        FROM\s+
        stg\.
        (?P<stg_table>
            [a-zA-Z_][a-zA-Z0-9_]*
        )
        \b
        """,
        re.IGNORECASE
        | re.DOTALL
        | re.VERBOSE,
    )

    match = pattern.search(sql)

    if match is None:
        raise RuntimeError(
            f"{transform_name}: "
            "unable to parse INSERT/SELECT"
        )

    parsed_ods = match.group(
        "ods_table"
    )

    parsed_stg = match.group(
        "stg_table"
    )

    if parsed_ods.lower() != (
        expected_ods_table.lower()
    ):
        raise RuntimeError(
            f"{transform_name}: "
            f"ODS mismatch: "
            f"metadata={expected_ods_table}, "
            f"sql={parsed_ods}"
        )

    if parsed_stg.lower() != (
        stg_table.lower()
    ):
        raise RuntimeError(
            f"{transform_name}: "
            f"STG mismatch: "
            f"metadata={stg_table}, "
            f"sql={parsed_stg}"
        )

    target_columns = [
        item.strip().strip('"')
        for item in split_top_level(
            match.group(
                "target_columns"
            )
        )
    ]

    select_expressions = (
        split_top_level(
            match.group(
                "select_list"
            )
        )
    )

    if len(target_columns) != len(
        select_expressions
    ):
        raise RuntimeError(
            f"{transform_name}: "
            "INSERT/SELECT column count mismatch: "
            f"target={len(target_columns)}, "
            f"select={len(select_expressions)}"
        )

    return (
        parsed_ods,
        target_columns,
        select_expressions,
    )


def classify_mapping(
    expression: str,
    source_columns: list[str],
    stg_technical_columns: list[str],
) -> str:
    """
    Classify the current STG -> ODS mapping.

    DIRECT:
        one source field copied without transformation

    CAST:
        one source field with a simple PostgreSQL cast

    DERIVED:
        business/calculated expression

    CONSTANT:
        literal value with no source input

    SYSTEM:
        DWH/STG technical metadata or raw payload retention
    """

    normalized = normalize_sql(
        expression
    )

    upper = normalized.upper()

    # --------------------------------------------------------
    # DWH/STG technical columns:
    #   _dwh_row_hash
    #   _dwh_batch_run_id
    #   _dwh_source_pk
    # etc.
    # --------------------------------------------------------

    if (
        stg_technical_columns
        and not source_columns
    ):
        return "SYSTEM"

    # Full raw STG payload retained in ODS.
    if normalized.lower() in {
        "s.source_data",
        "source_data",
    }:
        return "SYSTEM"

    # --------------------------------------------------------
    # No physical source field.
    # --------------------------------------------------------

    if not source_columns:
        if (
            "CURRENT_TIMESTAMP" in upper
            or "CURRENT_DATE" in upper
            or "NOW()" in upper
            or "CLOCK_TIMESTAMP()" in upper
        ):
            return "SYSTEM"

        return "CONSTANT"

    # --------------------------------------------------------
    # Multiple physical source fields means calculated mapping.
    #
    # Example:
    # rq_date + rs_date -> response_ms
    # --------------------------------------------------------

    if len(source_columns) > 1:
        return "DERIVED"

    source_column = source_columns[0]

    escaped_column = re.escape(
        source_column
    )

    simple_source_pattern = (
        r"(?:s\.)?"
        r"source_data"
        r"\s*->>\s*"
        r"'"
        + escaped_column
        + r"'"
    )

    # --------------------------------------------------------
    # Exact direct extraction:
    #
    # s.source_data->>'application_id'
    # --------------------------------------------------------

    if re.fullmatch(
        simple_source_pattern,
        normalized,
        flags=re.IGNORECASE,
    ):
        return "DIRECT"

    # --------------------------------------------------------
    # Exact simple cast:
    #
    # (s.source_data->>'id')::bigint
    # (s.source_data->>'created')::timestamp
    # --------------------------------------------------------

    simple_cast_pattern = (
        r"\(?\s*"
        + simple_source_pattern
        + r"\s*\)?"
        + r"\s*::\s*"
        + r"[a-zA-Z_][a-zA-Z0-9_]*"
        + r"(?:\s*\([^)]*\))?"
    )

    if re.fullmatch(
        simple_cast_pattern,
        normalized,
        flags=re.IGNORECASE,
    ):
        return "CAST"

    # --------------------------------------------------------
    # Everything else with source input is transformation.
    #
    # CASE, arithmetic, COALESCE, NULLIF, functions, etc.
    # --------------------------------------------------------

    return "DERIVED"

def load_catalog_state(
    conn,
) -> tuple[
    set[tuple[str, str, str]],
    set[tuple[str, str]],
]:
    """
    Source structure comes from captured CRM metadata.
    Target structure comes from the actual local ODS schema.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                source_schema,
                source_object,
                column_name
            FROM meta.v_latest_source_structure
            WHERE source_system_code =
                'CRM_RETAIL'
            """
        )

        source_columns = {
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
            )
            for row in cur.fetchall()
        }

        cur.execute(
            """
            SELECT
                table_name,
                column_name
            FROM information_schema.columns
            WHERE table_schema = 'ods'
            """
        )

        ods_columns = {
            (
                str(row[0]),
                str(row[1]),
            )
            for row in cur.fetchall()
        }

    return (
        source_columns,
        ods_columns,
    )


def main() -> None:
    module = runpy.run_path(
        str(TRANSFORM_PATH),
        run_name="mapping_audit",
    )

    transforms = module.get(
        "TRANSFORMS"
    )

    if not isinstance(
        transforms,
        list,
    ):
        raise RuntimeError(
            "TRANSFORMS not found "
            "or is not a list"
        )

    if len(transforms) != 12:
        raise RuntimeError(
            "Expected 12 TRANSFORMS, "
            f"found {len(transforms)}"
        )

    config = get_dwh_config()

    with connect(config) as conn:
        (
            catalog_source_columns,
            catalog_ods_columns,
        ) = load_catalog_state(conn)

    audit: list[dict] = []

    parse_errors: list[str] = []
    missing_source_refs: list[str] = []
    missing_target_columns: list[str] = []

    for transform_index, item in enumerate(
        transforms,
        start=1,
    ):
        name = str(
            item["name"]
        )

        stg_table = str(
            item["stg_table"]
        )

        ods_table = str(
            item["ods_table"]
        )

        sql = str(
            item["upsert_sql"]
        )

        if stg_table not in SOURCE_OBJECT_MAP:
            parse_errors.append(
                f"{name}: unknown STG table "
                f"{stg_table}"
            )
            continue

        (
            source_schema,
            source_object,
        ) = SOURCE_OBJECT_MAP[
            stg_table
        ]

        try:
            (
                parsed_ods,
                target_columns,
                select_expressions,
            ) = parse_upsert_sql(
                transform_name=name,
                stg_table=stg_table,
                expected_ods_table=ods_table,
                sql=sql,
            )

        except Exception as exc:
            parse_errors.append(
                f"{name}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )
            continue

        for mapping_position, (
            target_column,
            expression,
        ) in enumerate(
            zip(
                target_columns,
                select_expressions,
            ),
            start=1,
        ):
            source_refs = []

            for source_column in (
                SOURCE_DATA_RE.findall(
                    expression
                )
            ):
                if source_column not in source_refs:
                    source_refs.append(
                        source_column
                    )

            stg_tech_refs = []

            for tech_column in (
                STG_TECH_RE.findall(
                    expression
                )
            ):
                tech_column = (
                    tech_column.lower()
                )

                if (
                    tech_column
                    not in stg_tech_refs
                ):
                    stg_tech_refs.append(
                        tech_column
                    )

            mapping_type = classify_mapping(
                expression,
                source_refs,
                stg_tech_refs,
            )

            target_exists = (
                (
                    parsed_ods,
                    target_column,
                )
                in catalog_ods_columns
            )

            if not target_exists:
                missing_target_columns.append(
                    f"ods.{parsed_ods}."
                    f"{target_column}"
                )

            missing_sources = []

            for source_column in source_refs:
                source_key = (
                    source_schema,
                    source_object,
                    source_column,
                )

                if (
                    source_key
                    not in catalog_source_columns
                ):
                    missing_sources.append(
                        source_column
                    )

                    missing_source_refs.append(
                        f"{source_schema}."
                        f"{source_object}."
                        f"{source_column}"
                        f" -> "
                        f"ods.{parsed_ods}."
                        f"{target_column}"
                    )

            audit.append(
                {
                    "transform_index":
                        transform_index,

                    "mapping_position":
                        mapping_position,

                    "transform_name":
                        name,

                    "source_system_code":
                        "CRM_RETAIL",

                    "source_schema":
                        source_schema,

                    "source_object":
                        source_object,

                    "stg_schema":
                        "stg",

                    "stg_table":
                        stg_table,

                    "target_schema":
                        "ods",

                    "target_object":
                        parsed_ods,

                    "target_column":
                        target_column,

                    "source_columns":
                        source_refs,

                    "stg_technical_columns":
                        stg_tech_refs,

                    "select_expression":
                        normalize_sql(
                            expression
                        ),

                    "suggested_mapping_type":
                        mapping_type,

                    "source_refs_valid":
                        len(
                            missing_sources
                        ) == 0,

                    "missing_source_columns":
                        missing_sources,

                    "target_column_valid":
                        target_exists,
                }
            )

    # --------------------------------------------------------
    # Produce audit files.
    # --------------------------------------------------------

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    type_counts = Counter(
        row[
            "suggested_mapping_type"
        ]
        for row in audit
    )

    source_ref_count = sum(
        len(
            row["source_columns"]
        )
        for row in audit
    )

    audit_document = {
        "audit_version": 1,
        "source_system_code":
            "CRM_RETAIL",
        "transform_count":
            len(transforms),
        "mapping_count":
            len(audit),
        "source_reference_count":
            source_ref_count,
        "mapping_type_counts":
            dict(
                sorted(
                    type_counts.items()
                )
            ),
        "parse_errors":
            parse_errors,
        "missing_source_refs":
            sorted(
                set(
                    missing_source_refs
                )
            ),
        "missing_target_columns":
            sorted(
                set(
                    missing_target_columns
                )
            ),
        "mappings":
            audit,
    }

    OUTPUT_JSON.write_text(
        json.dumps(
            audit_document,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines: list[str] = []

    lines.append(
        "CRM_RETAIL MAPPING AUDIT"
    )
    lines.append(
        "=" * 80
    )
    lines.append(
        f"Transforms           : "
        f"{len(transforms)}"
    )
    lines.append(
        f"Mappings             : "
        f"{len(audit)}"
    )
    lines.append(
        f"Source references    : "
        f"{source_ref_count}"
    )

    for mapping_type in (
        "DIRECT",
        "CAST",
        "DERIVED",
        "CONSTANT",
        "SYSTEM",
    ):
        lines.append(
            f"{mapping_type:<20}: "
            f"{type_counts.get(mapping_type, 0)}"
        )

    lines.append(
        f"Parse errors         : "
        f"{len(parse_errors)}"
    )
    lines.append(
        f"Missing source refs  : "
        f"{len(set(missing_source_refs))}"
    )
    lines.append(
        f"Missing target cols  : "
        f"{len(set(missing_target_columns))}"
    )
    lines.append("")

    current_transform = None

    for row in audit:
        transform_name = row[
            "transform_name"
        ]

        if (
            transform_name
            != current_transform
        ):
            current_transform = (
                transform_name
            )

            lines.append(
                "=" * 80
            )
            lines.append(
                f"{transform_name}"
            )
            lines.append(
                f"{row['source_schema']}."
                f"{row['source_object']}"
                " -> "
                f"{row['target_schema']}."
                f"{row['target_object']}"
            )
            lines.append(
                "=" * 80
            )

        if row["source_columns"]:
            source_text = ", ".join(
                row["source_columns"]
            )
        elif row[
            "stg_technical_columns"
        ]:
            source_text = ", ".join(
                row[
                    "stg_technical_columns"
                ]
            )
        else:
            source_text = "(no source column)"

        lines.append(
            f"{row['mapping_position']:>3}. "
            f"{row['target_column']}"
        )

        lines.append(
            f"     source : "
            f"{source_text}"
        )

        lines.append(
            f"     type   : "
            f"{row['suggested_mapping_type']}"
        )

        lines.append(
            f"     expr   : "
            f"{row['select_expression']}"
        )

        lines.append("")

    if parse_errors:
        lines.append(
            "PARSE ERRORS"
        )
        lines.extend(
            parse_errors
        )
        lines.append("")

    if missing_source_refs:
        lines.append(
            "MISSING SOURCE REFERENCES"
        )
        lines.extend(
            sorted(
                set(
                    missing_source_refs
                )
            )
        )
        lines.append("")

    if missing_target_columns:
        lines.append(
            "MISSING TARGET COLUMNS"
        )
        lines.extend(
            sorted(
                set(
                    missing_target_columns
                )
            )
        )
        lines.append("")

    OUTPUT_TXT.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    print("MAPPING AUDIT COMPLETED")
    print(
        f"transforms          : "
        f"{len(transforms)}"
    )
    print(
        f"mappings            : "
        f"{len(audit)}"
    )
    print(
        f"source references   : "
        f"{source_ref_count}"
    )
    print(
        f"DIRECT              : "
        f"{type_counts.get('DIRECT', 0)}"
    )
    print(
        f"CAST                : "
        f"{type_counts.get('CAST', 0)}"
    )
    print(
        f"DERIVED             : "
        f"{type_counts.get('DERIVED', 0)}"
    )
    print(
        f"CONSTANT            : "
        f"{type_counts.get('CONSTANT', 0)}"
    )
    print(
        f"SYSTEM              : "
        f"{type_counts.get('SYSTEM', 0)}"
    )
    print(
        f"parse errors        : "
        f"{len(parse_errors)}"
    )
    print(
        f"missing source refs : "
        f"{len(set(missing_source_refs))}"
    )
    print(
        f"missing target cols : "
        f"{len(set(missing_target_columns))}"
    )
    print()
    print(
        f"TXT  : {OUTPUT_TXT}"
    )
    print(
        f"JSON : {OUTPUT_JSON}"
    )

    if (
        parse_errors
        or missing_source_refs
        or missing_target_columns
    ):
        raise SystemExit(2)

    print()
    print(
        "MAPPING AUDIT SUCCESS"
    )


if __name__ == "__main__":
    main()