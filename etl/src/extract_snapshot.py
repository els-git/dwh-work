from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dwh.config import get_crm_config
from dwh.db import connect_readonly


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_ROOT = PROJECT_ROOT / "data" / "snapshots"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def export_query(
    conn,
    query: str,
    params: tuple,
    output_file: Path,
) -> dict:
    sha256 = hashlib.sha256()
    row_count = 0
    raw_bytes = 0

    with conn.cursor() as cur:
        cur.execute(query, params)

        with gzip.open(
            output_file,
            mode="wt",
            encoding="utf-8",
            newline="\n",
        ) as gz:
            while True:
                rows = cur.fetchmany(500)

                if not rows:
                    break

                for row in rows:
                    payload = row[0]

                    line = json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ) + "\n"

                    encoded = line.encode("utf-8")

                    sha256.update(encoded)
                    raw_bytes += len(encoded)

                    gz.write(line)
                    row_count += 1

    return {
        "row_count": row_count,
        "sha256_uncompressed": sha256.hexdigest(),
        "uncompressed_bytes": raw_bytes,
        "compressed_bytes": output_file.stat().st_size,
        "file": output_file.name,
    }


def main() -> None:
    snapshot_date = os.getenv("SNAPSHOT_DATE")

    if not snapshot_date:
        raise RuntimeError("SNAPSHOT_DATE is not set")

    start = datetime.fromisoformat(snapshot_date)
    end = start + timedelta(days=1)

    final_dir = SNAPSHOT_ROOT / snapshot_date
    temp_dir = SNAPSHOT_ROOT / f".{snapshot_date}.tmp"

    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)

    if final_dir.exists():
        raise RuntimeError(
            f"Snapshot already exists: {final_dir}"
        )

    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir(parents=True)

    config = get_crm_config()

    manifest = {
        "snapshot_version": 1,
        "source_system": "CRM_RETAIL",
        "snapshot_date": snapshot_date,
        "extract_started_at_utc": utc_now(),
        "files": {},
    }

    try:
        with connect_readonly(config) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_database(),
                        current_user,
                        current_setting('transaction_read_only')
                    """
                )

                database, user, read_only = cur.fetchone()

                if read_only != "on":
                    raise RuntimeError(
                        "CRM connection is not READ ONLY"
                    )

                manifest["source_database"] = database
                manifest["source_user"] = user
                manifest["transaction_read_only"] = read_only

                cur.execute(
                    """
                    SELECT id
                    FROM app_retail.application
                    WHERE created >= %s
                      AND created < %s
                    ORDER BY id
                    """,
                    (start, end),
                )

                application_ids = [
                    str(row[0])
                    for row in cur.fetchall()
                ]

                cohort_hash = hashlib.sha256(
                    "\n".join(application_ids).encode("utf-8")
                ).hexdigest()

                manifest["cohort"] = {
                    "application_count": len(application_ids),
                    "application_ids_sha256": cohort_hash,
                }

            exports = [
                {
                    "key": "application",
                    "source": "app_retail.application",
                    "file": "application.jsonl.gz",
                    "excluded_columns": [],
                    "query": """
                        SELECT to_jsonb(a)
                        FROM app_retail.application a
                        WHERE a.created >= %s
                          AND a.created < %s
                        ORDER BY a.id
                    """,
                },
                {
                    "key": "applicant",
                    "source": "app_retail.applicant",
                    "file": "applicant.jsonl.gz",
                    "excluded_columns": [
                        "photo_branch_depr",
                        "photo_depr"
                    ],
                    "query": """
                        WITH selected_app AS (
                            SELECT id, applicant_id
                            FROM app_retail.application
                            WHERE created >= %s
                              AND created < %s
                        )
                        SELECT
                            to_jsonb(a)
                            - ARRAY[
                                'photo_branch_depr',
                                'photo_depr'
                              ]::text[]
                        FROM app_retail.applicant a
                        WHERE
                            a.application_id IN (
                                SELECT id
                                FROM selected_app
                            )
                            OR
                            a.id IN (
                                SELECT applicant_id
                                FROM selected_app
                                WHERE applicant_id IS NOT NULL
                            )
                        ORDER BY a.id
                    """,
                },
                {
                    "key": "credit_info",
                    "source": "app_retail.credit_info",
                    "file": "credit_info.jsonl.gz",
                    "excluded_columns": [],
                    "query": """
                        WITH selected_app AS (
                            SELECT
                                id,
                                requested_credit_info_id,
                                chosen_credit_info_id,
                                final_credit_info_id
                            FROM app_retail.application
                            WHERE created >= %s
                              AND created < %s
                        )
                        SELECT to_jsonb(ci)
                        FROM app_retail.credit_info ci
                        WHERE
                            ci.application_id IN (
                                SELECT id
                                FROM selected_app
                            )
                            OR
                            ci.id IN (
                                SELECT requested_credit_info_id
                                FROM selected_app
                                WHERE requested_credit_info_id IS NOT NULL

                                UNION

                                SELECT chosen_credit_info_id
                                FROM selected_app
                                WHERE chosen_credit_info_id IS NOT NULL

                                UNION

                                SELECT final_credit_info_id
                                FROM selected_app
                                WHERE final_credit_info_id IS NOT NULL
                            )
                        ORDER BY ci.id
                    """,
                },
                {
                    "key": "workflow_history",
                    "source": "app_retail.workflow_history",
                    "file": "workflow_history.jsonl.gz",
                    "excluded_columns": [],
                    "query": """
                        WITH selected_app AS (
                            SELECT id
                            FROM app_retail.application
                            WHERE created >= %s
                              AND created < %s
                        )
                        SELECT to_jsonb(wh)
                        FROM app_retail.workflow_history wh
                        WHERE wh.application_id IN (
                            SELECT id
                            FROM selected_app
                        )
                        ORDER BY wh.id
                    """,
                },
                {
                    "key": "integration_log",
                    "source": "integration_retail.integration_log",
                    "file": "integration_log.jsonl.gz",
                    "excluded_columns": [],
                    "query": """
                        WITH selected_app AS (
                            SELECT id, applicant_id
                            FROM app_retail.application
                            WHERE created >= %s
                              AND created < %s
                        ),
                        selected_applicant AS (
                            SELECT a.id
                            FROM app_retail.applicant a
                            WHERE
                                a.application_id IN (
                                    SELECT id
                                    FROM selected_app
                                )
                                OR
                                a.id IN (
                                    SELECT applicant_id
                                    FROM selected_app
                                    WHERE applicant_id IS NOT NULL
                                )
                        )
                        SELECT to_jsonb(il)
                        FROM integration_retail.integration_log il
                        WHERE
                            il.application_id IN (
                                SELECT id
                                FROM selected_app
                            )
                            OR
                            il.applicant_id IN (
                                SELECT id
                                FROM selected_applicant
                            )
                        ORDER BY
                            il.application_id NULLS LAST,
                            il.applicant_id NULLS LAST,
                            il.service_code,
                            il.rq_date NULLS LAST,
                            il.rs_date NULLS LAST,
                            il.guid NULLS LAST
                    """,
                },
            ]

            references = [
                ("product", "app_retail.product"),
                ("stage", "app_retail.stage"),
                ("status", "app_retail.status"),
                ("currency", "app_retail.dir_currency"),
                ("sales_channel", "app_retail.dir_sales_channel"),
                ("branch", "app_retail.dir_branch"),
                ("users", "app_retail.users"),
            ]

            for key, source in references:
                exports.append(
                    {
                        "key": key,
                        "source": source,
                        "file": f"{key}.jsonl.gz",
                        "excluded_columns": [],
                        "query": f"""
                            SELECT to_jsonb(t)
                            FROM {source} t
                            ORDER BY t.id
                        """,
                    }
                )

            for item in exports:
                output_file = temp_dir / item["file"]

                print(
                    f"Exporting {item['source']} ...",
                    flush=True,
                )

                if item["key"] in {
                    "application",
                    "applicant",
                    "credit_info",
                    "workflow_history",
                    "integration_log",
                }:
                    params = (start, end)
                else:
                    params = ()

                stats = export_query(
                    conn,
                    item["query"],
                    params,
                    output_file,
                )

                stats["source_object"] = item["source"]
                stats["excluded_columns"] = item["excluded_columns"]

                manifest["files"][item["key"]] = stats

                print(
                    f"  rows: {stats['row_count']}",
                    flush=True,
                )

        manifest["extract_finished_at_utc"] = utc_now()

        manifest_path = temp_dir / "manifest.json"

        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        temp_dir.rename(final_dir)

        print()
        print("SNAPSHOT COMPLETE")
        print(f"path : {final_dir}")

        total_rows = sum(
            item["row_count"]
            for item in manifest["files"].values()
        )

        print(f"rows : {total_rows}")

    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        raise


if __name__ == "__main__":
    main()