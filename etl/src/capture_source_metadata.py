from __future__ import annotations

from dwh.config import (
    get_crm_config,
    get_dwh_config,
)
from dwh.db import (
    connect,
    connect_readonly,
)


SOURCE_SYSTEM_CODE = "CRM_RETAIL"


OBJECT_TYPE_SQL = """
SELECT
    c.oid,
    c.relkind,

    CASE c.relkind
        WHEN 'r' THEN 'TABLE'
        WHEN 'p' THEN 'PARTITIONED_TABLE'
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED_VIEW'
        WHEN 'f' THEN 'FOREIGN_TABLE'
        ELSE 'OTHER'
    END AS object_type

FROM pg_catalog.pg_class c

JOIN pg_catalog.pg_namespace n
  ON n.oid = c.relnamespace

WHERE n.nspname = %s
  AND c.relname = %s
"""


COLUMN_SQL = """
SELECT
    a.attnum AS ordinal_position,
    a.attname AS column_name,

    pg_catalog.format_type(
        a.atttypid,
        a.atttypmod
    ) AS formatted_type,

    t.typname AS udt_name,

    NOT a.attnotnull AS is_nullable,

    EXISTS (
        SELECT 1
        FROM pg_catalog.pg_index i
        WHERE i.indrelid = c.oid
          AND i.indisprimary = true
          AND a.attnum = ANY(i.indkey)
    ) AS is_primary_key,

    EXISTS (
        SELECT 1
        FROM pg_catalog.pg_index i
        WHERE i.indrelid = c.oid
          AND i.indisunique = true
          AND a.attnum = ANY(i.indkey)
    ) AS is_unique_key,

    (a.attidentity <> '') AS is_identity,
    (a.attgenerated <> '') AS is_generated,

    pg_catalog.pg_get_expr(
        ad.adbin,
        ad.adrelid
    ) AS default_expression,

    pg_catalog.col_description(
        c.oid,
        a.attnum
    ) AS column_comment

FROM pg_catalog.pg_class c

JOIN pg_catalog.pg_namespace n
  ON n.oid = c.relnamespace

JOIN pg_catalog.pg_attribute a
  ON a.attrelid = c.oid

JOIN pg_catalog.pg_type t
  ON t.oid = a.atttypid

LEFT JOIN pg_catalog.pg_attrdef ad
  ON ad.adrelid = c.oid
 AND ad.adnum = a.attnum

WHERE n.nspname = %s
  AND c.relname = %s

  AND a.attnum > 0
  AND NOT a.attisdropped

ORDER BY a.attnum
"""


def main() -> None:
    dwh_config = get_dwh_config()
    crm_config = get_crm_config()

    # ========================================================
    # DWH connection
    # ========================================================

    with connect(dwh_config) as dwh:

        with dwh.cursor() as cur:
            cur.execute(
                """
                SELECT
                    source_system_id
                FROM meta.source_system
                WHERE code = %s
                """,
                (SOURCE_SYSTEM_CODE,),
            )

            row = cur.fetchone()

        if row is None:
            raise RuntimeError(
                "Source system CRM_RETAIL "
                "is not registered"
            )

        source_system_id = int(row[0])

        with dwh.cursor() as cur:
            cur.execute(
                """
                SELECT
                    source_object_id,
                    source_schema,
                    source_object
                FROM meta.source_object
                WHERE source_system_id = %s
                ORDER BY source_object_id
                """,
                (source_system_id,),
            )

            source_objects = cur.fetchall()

        if not source_objects:
            raise RuntimeError(
                "No CRM_RETAIL source objects"
            )

        # ====================================================
        # CRM connection -- enforced READ ONLY
        # ====================================================

        with connect_readonly(
            crm_config
        ) as crm:

            with crm.cursor() as cur:
                cur.execute(
                    """
                    SHOW transaction_read_only
                    """
                )

                readonly = cur.fetchone()[0]

                cur.execute(
                    """
                    SELECT
                        current_database(),
                        current_setting(
                            'server_version'
                        )
                    """
                )

                (
                    source_database,
                    source_version,
                ) = cur.fetchone()

            if readonly != "on":
                raise RuntimeError(
                    "CRM connection is not READ ONLY"
                )

            # ================================================
            # Create new immutable capture
            # ================================================

            with dwh.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO meta.metadata_capture (
                        source_system_id,
                        status,
                        started_at,
                        source_database,
                        source_version,
                        created_by,
                        message
                    )
                    VALUES (
                        %s,
                        'RUNNING',
                        now(),
                        %s,
                        %s,
                        'capture_source_metadata',
                        'Source schema metadata capture'
                    )
                    RETURNING metadata_capture_id
                    """,
                    (
                        source_system_id,
                        source_database,
                        source_version,
                    ),
                )

                metadata_capture_id = int(
                    cur.fetchone()[0]
                )

            dwh.commit()

            print("METADATA CAPTURE STARTED")
            print(
                f"capture_id : "
                f"{metadata_capture_id}"
            )
            print(
                f"objects    : "
                f"{len(source_objects)}"
            )
            print(
                f"CRM readonly: {readonly}"
            )
            print()

            object_count = 0
            column_count = 0

            try:
                for (
                    source_object_id,
                    source_schema,
                    source_object,
                ) in source_objects:

                    # ----------------------------------------
                    # Object type
                    # ----------------------------------------

                    with crm.cursor() as cur:
                        cur.execute(
                            OBJECT_TYPE_SQL,
                            (
                                source_schema,
                                source_object,
                            ),
                        )

                        object_row = cur.fetchone()

                    if object_row is None:
                        raise RuntimeError(
                            "Source object not found: "
                            f"{source_schema}."
                            f"{source_object}"
                        )

                    (
                        object_oid,
                        relkind,
                        object_type,
                    ) = object_row

                    # ----------------------------------------
                    # Columns
                    # ----------------------------------------

                    with crm.cursor() as cur:
                        cur.execute(
                            COLUMN_SQL,
                            (
                                source_schema,
                                source_object,
                            ),
                        )

                        columns = cur.fetchall()

                    if not columns:
                        raise RuntimeError(
                            "Source object has no columns: "
                            f"{source_schema}."
                            f"{source_object}"
                        )

                    # ----------------------------------------
                    # Object snapshot
                    # ----------------------------------------

                    with dwh.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO
                                meta.source_object_snapshot (
                                    metadata_capture_id,
                                    source_object_id,
                                    source_schema,
                                    source_object,
                                    relkind,
                                    object_type
                                )
                            VALUES (
                                %s,
                                %s,
                                %s,
                                %s,
                                %s,
                                %s
                            )
                            RETURNING object_snapshot_id
                            """,
                            (
                                metadata_capture_id,
                                source_object_id,
                                source_schema,
                                source_object,
                                relkind,
                                object_type,
                            ),
                        )

                        object_snapshot_id = int(
                            cur.fetchone()[0]
                        )

                    # ----------------------------------------
                    # Column snapshots
                    # ----------------------------------------

                    with dwh.cursor() as cur:
                        for column in columns:
                            (
                                ordinal_position,
                                column_name,
                                formatted_type,
                                udt_name,
                                is_nullable,
                                is_primary_key,
                                is_unique_key,
                                is_identity,
                                is_generated,
                                default_expression,
                                column_comment,
                            ) = column

                            cur.execute(
                                """
                                INSERT INTO
                                    meta.source_column_snapshot (
                                        object_snapshot_id,
                                        ordinal_position,
                                        column_name,
                                        formatted_type,
                                        udt_name,
                                        is_nullable,
                                        is_primary_key,
                                        is_unique_key,
                                        is_identity,
                                        is_generated,
                                        default_expression,
                                        column_comment
                                    )
                                VALUES (
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s,
                                    %s
                                )
                                """,
                                (
                                    object_snapshot_id,
                                    ordinal_position,
                                    column_name,
                                    formatted_type,
                                    udt_name,
                                    is_nullable,
                                    is_primary_key,
                                    is_unique_key,
                                    is_identity,
                                    is_generated,
                                    default_expression,
                                    column_comment,
                                ),
                            )

                    dwh.commit()

                    object_count += 1
                    column_count += len(columns)

                    print(
                        f"CAPTURED "
                        f"{source_schema}."
                        f"{source_object:<28} "
                        f"columns="
                        f"{len(columns)}"
                    )

                # ============================================
                # Complete capture
                # ============================================

                with dwh.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE meta.metadata_capture
                        SET
                            status = 'SUCCESS',
                            finished_at = now(),
                            objects_captured = %s,
                            columns_captured = %s,
                            message = %s
                        WHERE metadata_capture_id = %s
                        """,
                        (
                            object_count,
                            column_count,
                            (
                                "Metadata capture SUCCESS; "
                                f"objects={object_count}; "
                                f"columns={column_count}"
                            ),
                            metadata_capture_id,
                        ),
                    )

                dwh.commit()

                print()
                print("METADATA CAPTURE SUCCESS")
                print(
                    f"capture_id : "
                    f"{metadata_capture_id}"
                )
                print(
                    f"objects    : "
                    f"{object_count}"
                )
                print(
                    f"columns    : "
                    f"{column_count}"
                )

            except Exception as exc:
                dwh.rollback()

                with dwh.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE meta.metadata_capture
                        SET
                            status = 'FAILED',
                            finished_at = now(),
                            objects_captured = %s,
                            columns_captured = %s,
                            message = %s
                        WHERE metadata_capture_id = %s
                        """,
                        (
                            object_count,
                            column_count,
                            (
                                f"{type(exc).__name__}: "
                                f"{str(exc)[:3000]}"
                            ),
                            metadata_capture_id,
                        ),
                    )

                dwh.commit()
                raise


if __name__ == "__main__":
    main()