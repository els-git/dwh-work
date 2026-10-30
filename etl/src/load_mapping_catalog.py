from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from dwh.config import get_dwh_config
from dwh.db import connect


PROJECT_ROOT = Path(__file__).resolve().parents[2]

AUDIT_PATH = (
    PROJECT_ROOT
    / "runtime"
    / "temp"
    / "integration"
    / "mapping_audit"
    / "mapping_audit.json"
)

SOURCE_SYSTEM_CODE = "CRM_RETAIL"
MAPPING_STAGE = "STG_TO_ODS"

EXPECTED_MAPPING_COUNT = 207
EXPECTED_PHYSICAL_SOURCE_REFS = 172

EXPECTED_TYPES = {
    "DIRECT": 75,
    "CAST": 95,
    "DERIVED": 1,
    "CONSTANT": 0,
    "SYSTEM": 36,
}


def definition_hash(
    payload: dict,
) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def load_ods_types(
    conn,
) -> dict[tuple[str, str], str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                c.relname,
                a.attname,
                pg_catalog.format_type(
                    a.atttypid,
                    a.atttypmod
                )
            FROM pg_catalog.pg_class c

            JOIN pg_catalog.pg_namespace n
              ON n.oid = c.relnamespace

            JOIN pg_catalog.pg_attribute a
              ON a.attrelid = c.oid

            WHERE n.nspname = 'ods'
              AND a.attnum > 0
              AND NOT a.attisdropped
            """
        )

        return {
            (
                str(table_name),
                str(column_name),
            ): str(pg_type)
            for (
                table_name,
                column_name,
                pg_type,
            ) in cur.fetchall()
        }


def build_catalog_rows(
    audit_document: dict,
    ods_types: dict[
        tuple[str, str],
        str,
    ],
) -> list[dict]:
    result = []

    for row in audit_document["mappings"]:
        target_object = str(
            row["target_object"]
        )

        target_column = str(
            row["target_column"]
        )

        type_key = (
            target_object,
            target_column,
        )

        if type_key not in ods_types:
            raise RuntimeError(
                "ODS type not found: "
                f"ods.{target_object}."
                f"{target_column}"
            )

        mapping_key = (
            f"{SOURCE_SYSTEM_CODE}"
            f"|{MAPPING_STAGE}"
            f"|ods.{target_object}."
            f"{target_column}"
        )

        source_refs = []

        # Physical CRM source fields.
        for position, source_column in enumerate(
            row["source_columns"],
            start=1,
        ):
            source_refs.append(
                {
                    "source_layer":
                        "SOURCE",
                    "source_schema":
                        row["source_schema"],
                    "source_object":
                        row["source_object"],
                    "source_column":
                        source_column,
                    "ordinal_position":
                        position,
                }
            )

        # STG technical metadata.
        if not source_refs:
            for position, stg_column in enumerate(
                row[
                    "stg_technical_columns"
                ],
                start=1,
            ):
                source_refs.append(
                    {
                        "source_layer":
                            "STG",
                        "source_schema":
                            "stg",
                        "source_object":
                            row["stg_table"],
                        "source_column":
                            stg_column,
                        "ordinal_position":
                            position,
                    }
                )

        # Full raw payload retention:
        # s.source_data -> ODS source_data.
        expression = str(
            row["select_expression"]
        ).strip()

        if (
            not source_refs
            and expression.lower()
            in {
                "s.source_data",
                "source_data",
            }
        ):
            source_refs.append(
                {
                    "source_layer":
                        "STG",
                    "source_schema":
                        "stg",
                    "source_object":
                        row["stg_table"],
                    "source_column":
                        "source_data",
                    "ordinal_position":
                        1,
                }
            )

        semantic_definition = {
            "mapping_key":
                mapping_key,

            "transform_name":
                row["transform_name"],

            "mapping_position":
                int(
                    row[
                        "mapping_position"
                    ]
                ),

            "stg_schema":
                row["stg_schema"],

            "stg_table":
                row["stg_table"],

            "target_schema":
                row["target_schema"],

            "target_object":
                target_object,

            "target_column":
                target_column,

            "mapping_type":
                row[
                    "suggested_mapping_type"
                ],

            "transform_expression":
                expression,

            "expected_pg_type":
                ods_types[type_key],

            "source_refs":
                source_refs,
        }

        result.append(
            {
                **semantic_definition,
                "definition_hash":
                    definition_hash(
                        semantic_definition
                    ),
            }
        )

    return result


def validate_audit(
    audit_document: dict,
) -> None:
    if int(
        audit_document.get(
            "transform_count",
            -1,
        )
    ) != 12:
        raise RuntimeError(
            "Mapping audit transform_count "
            "is not 12"
        )

    if int(
        audit_document.get(
            "mapping_count",
            -1,
        )
    ) != EXPECTED_MAPPING_COUNT:
        raise RuntimeError(
            "Mapping audit mapping_count "
            f"is not {EXPECTED_MAPPING_COUNT}"
        )

    if audit_document.get(
        "parse_errors"
    ):
        raise RuntimeError(
            "Mapping audit has parse errors"
        )

    if audit_document.get(
        "missing_source_refs"
    ):
        raise RuntimeError(
            "Mapping audit has missing source refs"
        )

    if audit_document.get(
        "missing_target_columns"
    ):
        raise RuntimeError(
            "Mapping audit has missing target columns"
        )

    type_counts = Counter(
        str(
            row[
                "suggested_mapping_type"
            ]
        )
        for row in audit_document[
            "mappings"
        ]
    )

    actual_types = {
        mapping_type:
            int(
                type_counts.get(
                    mapping_type,
                    0,
                )
            )
        for mapping_type
        in EXPECTED_TYPES
    }

    if actual_types != EXPECTED_TYPES:
        raise RuntimeError(
            "Unexpected mapping type counts: "
            f"{actual_types}"
        )

    physical_source_refs = sum(
        len(row["source_columns"])
        for row in audit_document[
            "mappings"
        ]
    )

    if (
        physical_source_refs
        != EXPECTED_PHYSICAL_SOURCE_REFS
    ):
        raise RuntimeError(
            "Unexpected physical source "
            "reference count: "
            f"{physical_source_refs}"
        )


def current_catalog(
    conn,
) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                mapping_key,
                definition_hash
            FROM meta.v_current_mapping
            WHERE source_system_code = %s
              AND mapping_stage = %s
              AND is_active = true
            """,
            (
                SOURCE_SYSTEM_CODE,
                MAPPING_STAGE,
            ),
        )

        return {
            str(mapping_key):
                str(def_hash)
            for (
                mapping_key,
                def_hash,
            ) in cur.fetchall()
        }


def main() -> None:
    if not AUDIT_PATH.exists():
        raise RuntimeError(
            f"Mapping audit JSON not found: "
            f"{AUDIT_PATH}"
        )

    audit_document = json.loads(
        AUDIT_PATH.read_text(
            encoding="utf-8"
        )
    )

    validate_audit(
        audit_document
    )

    config = get_dwh_config()

    with connect(config) as conn:
        ods_types = load_ods_types(
            conn
        )

        catalog_rows = (
            build_catalog_rows(
                audit_document,
                ods_types,
            )
        )

        if len(catalog_rows) != (
            EXPECTED_MAPPING_COUNT
        ):
            raise RuntimeError(
                "Prepared mapping count "
                f"is {len(catalog_rows)}, "
                f"expected "
                f"{EXPECTED_MAPPING_COUNT}"
            )

        expected_catalog = {
            row["mapping_key"]:
                row["definition_hash"]
            for row in catalog_rows
        }

        existing = current_catalog(
            conn
        )

        # Idempotent bootstrap:
        # identical catalog => no new history row.
        if existing:
            if existing == expected_catalog:
                print(
                    "MAPPING CATALOG "
                    "ALREADY CURRENT"
                )
                print(
                    f"mappings : "
                    f"{len(existing)}"
                )
                return

            raise RuntimeError(
                "Mapping catalog is already "
                "populated but differs from "
                "the audited configuration. "
                "Bootstrap will not overwrite "
                "or version existing mappings."
            )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT clock_timestamp()"
            )

            valid_from = (
                cur.fetchone()[0]
            )

        inserted_mappings = 0
        inserted_refs = 0

        try:
            for row in catalog_rows:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO meta.mapping_rule (
                            mapping_key,
                            version_no,

                            source_system_code,
                            mapping_stage,

                            target_schema,
                            target_object,
                            target_column,

                            mapping_type,
                            transform_expression,
                            expected_pg_type,

                            is_active,

                            valid_from,
                            valid_to,

                            supersedes_mapping_id,

                            created_by,
                            change_reason,

                            transform_name,
                            mapping_position,

                            stg_schema,
                            stg_table,

                            definition_hash
                        )
                        VALUES (
                            %s,
                            1,

                            %s,
                            %s,

                            %s,
                            %s,
                            %s,

                            %s,
                            %s,
                            %s,

                            true,

                            %s,
                            NULL,

                            NULL,

                            'mapping_bootstrap_v1',
                            %s,

                            %s,
                            %s,

                            %s,
                            %s,

                            %s
                        )
                        RETURNING mapping_id
                        """,
                        (
                            row["mapping_key"],

                            SOURCE_SYSTEM_CODE,
                            MAPPING_STAGE,

                            row["target_schema"],
                            row["target_object"],
                            row["target_column"],

                            row["mapping_type"],
                            row[
                                "transform_expression"
                            ],
                            row[
                                "expected_pg_type"
                            ],

                            valid_from,

                            (
                                "Initial audited "
                                "STG_TO_ODS mapping "
                                "catalog"
                            ),

                            row["transform_name"],
                            row["mapping_position"],

                            row["stg_schema"],
                            row["stg_table"],

                            row["definition_hash"],
                        ),
                    )

                    mapping_id = int(
                        cur.fetchone()[0]
                    )

                    for source_ref in (
                        row["source_refs"]
                    ):
                        cur.execute(
                            """
                            INSERT INTO
                                meta.mapping_source_ref (
                                    mapping_id,
                                    source_schema,
                                    source_object,
                                    source_column,
                                    source_role,
                                    ordinal_position,
                                    source_layer
                                )
                            VALUES (
                                %s,
                                %s,
                                %s,
                                %s,
                                'INPUT',
                                %s,
                                %s
                            )
                            """,
                            (
                                mapping_id,

                                source_ref[
                                    "source_schema"
                                ],
                                source_ref[
                                    "source_object"
                                ],
                                source_ref[
                                    "source_column"
                                ],

                                source_ref[
                                    "ordinal_position"
                                ],

                                source_ref[
                                    "source_layer"
                                ],
                            ),
                        )

                        inserted_refs += 1

                inserted_mappings += 1

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        print("MAPPING CATALOG BOOTSTRAP SUCCESS")
        print(
            f"mappings         : "
            f"{inserted_mappings}"
        )
        print(
            f"source refs      : "
            f"{inserted_refs}"
        )
        print(
            f"physical refs    : "
            f"{EXPECTED_PHYSICAL_SOURCE_REFS}"
        )
        print(
            f"DIRECT           : "
            f"{EXPECTED_TYPES['DIRECT']}"
        )
        print(
            f"CAST             : "
            f"{EXPECTED_TYPES['CAST']}"
        )
        print(
            f"DERIVED          : "
            f"{EXPECTED_TYPES['DERIVED']}"
        )
        print(
            f"CONSTANT         : "
            f"{EXPECTED_TYPES['CONSTANT']}"
        )
        print(
            f"SYSTEM           : "
            f"{EXPECTED_TYPES['SYSTEM']}"
        )


if __name__ == "__main__":
    main()