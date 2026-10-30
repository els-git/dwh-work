from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import date
from pathlib import Path

from psycopg import sql
from psycopg.types.json import Jsonb

from dwh.config import get_dwh_config
from dwh.db import connect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_ROOT = PROJECT_ROOT / "data" / "snapshots"


def canonical_hash(row: dict) -> str:
    payload = json.dumps(
        row,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


def verify_snapshot(snapshot_dir: Path, manifest: dict) -> None:
    print("VERIFYING SNAPSHOT")

    total_rows = 0

    for key, info in manifest["files"].items():
        file_path = snapshot_dir / info["file"]

        if not file_path.exists():
            raise RuntimeError(
                f"Snapshot file is missing: {file_path}"
            )

        digest = hashlib.sha256()
        row_count = 0

        with gzip.open(file_path, "rb") as source:
            for line in source:
                digest.update(line)
                row_count += 1

        actual_hash = digest.hexdigest()
        expected_hash = info["sha256_uncompressed"]
        expected_rows = int(info["row_count"])

        if row_count != expected_rows:
            raise RuntimeError(
                f"{key}: row count mismatch: "
                f"expected={expected_rows}, actual={row_count}"
            )

        if actual_hash != expected_hash:
            raise RuntimeError(
                f"{key}: SHA256 mismatch"
            )

        total_rows += row_count

        print(
            f"{key:<20} rows={row_count:<6} SHA256 OK"
        )

    print()
    print(f"SNAPSHOT VERIFIED: {total_rows} rows")
    print()


def resolve_load_plan(conn, manifest: dict) -> list[dict]:
    plan = []

    with conn.cursor() as cur:
        for key, info in manifest["files"].items():
            source_schema, source_object = (
                info["source_object"].split(".", 1)
            )

            cur.execute(
                """
                SELECT
                    j.job_id,
                    so.source_object_id,
                    so.watermark_column,
                    j.target_schema,
                    j.target_object,
                    j.execution_order
                FROM etl.job j
                JOIN meta.source_object so
                  ON so.source_object_id = j.source_object_id
                JOIN meta.source_system ss
                  ON ss.source_system_id = so.source_system_id
                WHERE ss.code = 'CRM_RETAIL'
                  AND so.source_schema = %s
                  AND so.source_object = %s
                  AND j.job_type = 'EXTRACT'
                  AND j.is_active = true
                """,
                (source_schema, source_object),
            )

            row = cur.fetchone()

            if row is None:
                raise RuntimeError(
                    f"No ETL job registered for "
                    f"{info['source_object']}"
                )

            (
                job_id,
                source_object_id,
                watermark_column,
                target_schema,
                target_object,
                execution_order,
            ) = row

            if target_schema != "stg":
                raise RuntimeError(
                    f"Invalid target schema for "
                    f"{info['source_object']}: "
                    f"{target_schema}"
                )

            if not target_object.startswith("crm_retail_"):
                raise RuntimeError(
                    f"Unexpected target object: {target_object}"
                )

            plan.append(
                {
                    "key": key,
                    "info": info,
                    "job_id": job_id,
                    "source_object_id": source_object_id,
                    "watermark_column": watermark_column,
                    "target_schema": target_schema,
                    "target_object": target_object,
                    "execution_order": execution_order,
                }
            )

    return sorted(
        plan,
        key=lambda item: item["execution_order"],
    )


def get_source_pk(key: str, row: dict, row_hash: str) -> str:
    value = row.get("id")

    if value is not None:
        return str(value)

    # integration_log is a VIEW and does not have
    # a guaranteed physical primary key.
    return row_hash


def get_application_id(key: str, row: dict):
    if key == "application":
        return row.get("id")

    return row.get("application_id")


def load_file(
    conn,
    item: dict,
    snapshot_dir: Path,
    batch_run_id: int,
    job_run_id: int,
) -> int:

    file_path = snapshot_dir / item["info"]["file"]

    insert_query = sql.SQL(
        """
        INSERT INTO {}.{} (
            _dwh_batch_run_id,
            _dwh_job_run_id,
            _dwh_source_pk,
            _dwh_application_id,
            _dwh_watermark_ts,
            _dwh_row_hash,
            source_data
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s::timestamp,
            %s,
            %s
        )
        """
    ).format(
        sql.Identifier(item["target_schema"]),
        sql.Identifier(item["target_object"]),
    )

    inserted = 0
    buffer = []

    with gzip.open(
        file_path,
        "rt",
        encoding="utf-8",
    ) as source:

        with conn.cursor() as cur:
            for line in source:
                row = json.loads(line)

                row_hash = canonical_hash(row)

                source_pk = get_source_pk(
                    item["key"],
                    row,
                    row_hash,
                )

                application_id = get_application_id(
                    item["key"],
                    row,
                )

                watermark_value = None

                if item["watermark_column"]:
                    watermark_value = row.get(
                        item["watermark_column"]
                    )

                buffer.append(
                    (
                        batch_run_id,
                        job_run_id,
                        source_pk,
                        application_id,
                        watermark_value,
                        row_hash,
                        Jsonb(row),
                    )
                )

                if len(buffer) >= 500:
                    cur.executemany(
                        insert_query,
                        buffer,
                    )

                    inserted += len(buffer)
                    buffer.clear()

            if buffer:
                cur.executemany(
                    insert_query,
                    buffer,
                )

                inserted += len(buffer)

    return inserted


def main() -> None:
    snapshot_date = os.getenv("SNAPSHOT_DATE")

    if not snapshot_date:
        raise RuntimeError("SNAPSHOT_DATE is not set")

    snapshot_dir = SNAPSHOT_ROOT / snapshot_date
    manifest_path = snapshot_dir / "manifest.json"

    if not manifest_path.exists():
        raise RuntimeError(
            f"Manifest not found: {manifest_path}"
        )

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if manifest["snapshot_date"] != snapshot_date:
        raise RuntimeError(
            "Snapshot date does not match manifest"
        )

    if manifest.get("transaction_read_only") != "on":
        raise RuntimeError(
            "Snapshot was not extracted using a READ ONLY connection"
        )

    # --------------------------------------------------------
    # VERIFY ALL SNAPSHOT FILES BEFORE TOUCHING DWH
    # --------------------------------------------------------

    verify_snapshot(snapshot_dir, manifest)

    config = get_dwh_config()

    batch_code = (
        "CRM_RETAIL_SNAPSHOT_"
        + snapshot_date.replace("-", "")
    )

    batch_run_id = None

    with connect(config) as conn:

        # ----------------------------------------------------
        # Confirm correct destination
        # ----------------------------------------------------

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    current_database(),
                    current_user
                """
            )

            database, user = cur.fetchone()

            if database != "dwh":
                raise RuntimeError(
                    f"Unexpected database: {database}"
                )

            if user != "dwh_etl":
                raise RuntimeError(
                    f"Unexpected DWH user: {user}"
                )

        # ----------------------------------------------------
        # Prevent accidental duplicate successful load
        # ----------------------------------------------------

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT batch_run_id
                FROM etl.batch_run
                WHERE batch_code = %s
                  AND business_date = %s
                  AND status = 'SUCCESS'
                ORDER BY batch_run_id DESC
                LIMIT 1
                """,
                (
                    batch_code,
                    date.fromisoformat(snapshot_date),
                ),
            )

            existing = cur.fetchone()

            if existing:
                raise RuntimeError(
                    f"Snapshot {snapshot_date} was already "
                    f"successfully loaded. "
                    f"batch_run_id={existing[0]}"
                )

        # ----------------------------------------------------
        # Resolve registered ETL jobs
        # ----------------------------------------------------

        plan = resolve_load_plan(
            conn,
            manifest,
        )

        # ----------------------------------------------------
        # Create batch
        # ----------------------------------------------------

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO etl.batch_run (
                    batch_code,
                    business_date,
                    trigger_type,
                    status,
                    started_at,
                    created_by,
                    message
                )
                VALUES (
                    %s,
                    %s,
                    'MANUAL',
                    'RUNNING',
                    now(),
                    'snapshot_loader',
                    %s
                )
                RETURNING batch_run_id
                """,
                (
                    batch_code,
                    date.fromisoformat(snapshot_date),
                    "CRM Retail local snapshot load",
                ),
            )

            batch_run_id = cur.fetchone()[0]

        conn.commit()

        print(f"BATCH STARTED: {batch_run_id}")
        print()

        try:
            for item in plan:

                expected_rows = int(
                    item["info"]["row_count"]
                )

                # --------------------------------------------
                # Create job run
                # --------------------------------------------

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO etl.job_run (
                            batch_run_id,
                            job_id,
                            status,
                            started_at
                        )
                        VALUES (
                            %s,
                            %s,
                            'RUNNING',
                            now()
                        )
                        RETURNING job_run_id
                        """,
                        (
                            batch_run_id,
                            item["job_id"],
                        ),
                    )

                    job_run_id = cur.fetchone()[0]

                conn.commit()

                print(
                    f"Loading "
                    f"{item['info']['source_object']} "
                    f"-> "
                    f"{item['target_schema']}."
                    f"{item['target_object']}"
                )

                try:
                    inserted = load_file(
                        conn,
                        item,
                        snapshot_dir,
                        batch_run_id,
                        job_run_id,
                    )

                    if inserted != expected_rows:
                        raise RuntimeError(
                            f"Inserted row count mismatch: "
                            f"expected={expected_rows}, "
                            f"actual={inserted}"
                        )

                    # DB-side reconciliation
                    with conn.cursor() as cur:
                        count_query = sql.SQL(
                            """
                            SELECT COUNT(*)
                            FROM {}.{}
                            WHERE _dwh_job_run_id = %s
                            """
                        ).format(
                            sql.Identifier(
                                item["target_schema"]
                            ),
                            sql.Identifier(
                                item["target_object"]
                            ),
                        )

                        cur.execute(
                            count_query,
                            (job_run_id,),
                        )

                        db_count = cur.fetchone()[0]

                    if db_count != expected_rows:
                        raise RuntimeError(
                            f"Database reconciliation failed: "
                            f"expected={expected_rows}, "
                            f"actual={db_count}"
                        )

                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE etl.job_run
                            SET
                                status = 'SUCCESS',
                                finished_at = now(),
                                rows_read = %s,
                                rows_inserted = %s,
                                message = %s
                            WHERE job_run_id = %s
                            """,
                            (
                                expected_rows,
                                inserted,
                                f"Loaded snapshot {snapshot_date}",
                                job_run_id,
                            ),
                        )

                    conn.commit()

                    print(
                        f"  SUCCESS rows={inserted}"
                    )

                except Exception as exc:
                    conn.rollback()

                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE etl.job_run
                            SET
                                status = 'FAILED',
                                finished_at = now(),
                                error_count = 1,
                                message = %s
                            WHERE job_run_id = %s
                            """,
                            (
                                str(exc)[:4000],
                                job_run_id,
                            ),
                        )

                        cur.execute(
                            """
                            INSERT INTO etl.error_log (
                                batch_run_id,
                                job_run_id,
                                job_id,
                                source_object_id,
                                error_stage,
                                error_code,
                                error_message
                            )
                            VALUES (
                                %s,
                                %s,
                                %s,
                                %s,
                                'STG_LOAD',
                                %s,
                                %s
                            )
                            """,
                            (
                                batch_run_id,
                                job_run_id,
                                item["job_id"],
                                item["source_object_id"],
                                type(exc).__name__,
                                str(exc)[:4000],
                            ),
                        )

                    conn.commit()

                    raise

            # ------------------------------------------------
            # Batch success
            # ------------------------------------------------

            total_rows = sum(
                int(item["info"]["row_count"])
                for item in plan
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE etl.batch_run
                    SET
                        status = 'SUCCESS',
                        finished_at = now(),
                        message = %s
                    WHERE batch_run_id = %s
                    """,
                    (
                        f"Snapshot {snapshot_date}; "
                        f"rows={total_rows}",
                        batch_run_id,
                    ),
                )

            conn.commit()

            print()
            print("BATCH SUCCESS")
            print(f"batch_run_id : {batch_run_id}")
            print(f"rows         : {total_rows}")

        except Exception as exc:
            conn.rollback()

            if batch_run_id is not None:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE etl.batch_run
                        SET
                            status = 'FAILED',
                            finished_at = now(),
                            message = %s
                        WHERE batch_run_id = %s
                        """,
                        (
                            str(exc)[:4000],
                            batch_run_id,
                        ),
                    )

                conn.commit()

            print()
            print("BATCH FAILED")
            raise


if __name__ == "__main__":
    main()