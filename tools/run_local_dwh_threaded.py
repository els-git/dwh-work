from __future__ import annotations

import importlib.util
import os
import re
import socket
import sys
import threading
import traceback
import warnings

from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parent.parent

ENV_FILE = PROJECT / ".env"

TIMEZONE_DIR = Path(
    r"C:\Program Files\PostgreSQL\17\share\timezone"
)

SOURCE_PATHS = (
    PROJECT / "backend" / "src",
    PROJECT / "backend" / "packages",
    PROJECT / "etl" / "src",
    PROJECT / "etl" / "packages",
)

HOST = "127.0.0.1"
PORT = 8000
POLL_SECONDS = 5


def log(message: str) -> None:
    print(message, flush=True)


def load_env() -> int:
    if not ENV_FILE.is_file():
        raise RuntimeError(
            "Project .env not found"
        )

    count = 0

    for line_number, raw in enumerate(
        ENV_FILE.read_text(
            encoding="utf-8-sig",
        ).splitlines(),
        start=1,
    ):
        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        match = re.fullmatch(
            r"(?:export\s+)?"
            r"([A-Za-z_][A-Za-z0-9_]*)"
            r"\s*=\s*(.*)",
            line,
        )

        if match is None:
            raise RuntimeError(
                "Unsupported .env syntax at line "
                + str(line_number)
            )

        key = match.group(1)
        value = match.group(2).strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]

        elif value:
            value = re.sub(
                r"\s+#.*$",
                "",
                value,
            ).strip()

        # Only changes the current Python process.
        # No passwords are printed or written to reports.
        os.environ[key] = value
        count += 1

    return count


def configure_python() -> None:
    for path in SOURCE_PATHS:
        if not path.is_dir():
            raise RuntimeError(
                "Python package directory missing: "
                + str(path)
            )

    # Preserve the same import priority as the earlier
    # unified launcher.
    for path in reversed(SOURCE_PATHS):
        value = str(path)

        if value not in sys.path:
            sys.path.insert(0, value)

    baku_file = TIMEZONE_DIR / "Asia" / "Baku"

    if not baku_file.is_file():
        raise RuntimeError(
            "PostgreSQL Asia/Baku timezone file missing"
        )

    os.environ["PYTHONTZPATH"] = str(TIMEZONE_DIR)

    # Refresh ZoneInfo in case Python loaded it earlier.
    import zoneinfo

    zoneinfo.reset_tzpath(
        [str(TIMEZONE_DIR)]
    )

    zoneinfo.ZoneInfo.clear_cache()


def verify_environment() -> None:
    required = (
        "DWH_DB_NAME",
        "DWH_DB_PORT",
        "DWH_ETL_PASSWORD",
        "DWH_APP_PASSWORD",
    )

    for key in required:
        if not os.environ.get(key):
            raise RuntimeError(
                "Required .env variable missing: "
                + key
            )

    if os.environ["DWH_DB_NAME"] != "dwh":
        raise RuntimeError(
            "Unexpected DWH database name"
        )

    if os.environ["DWH_DB_PORT"] != "5433":
        raise RuntimeError(
            "Unexpected DWH database port"
        )

    from zoneinfo import ZoneInfo

    baku = ZoneInfo("Asia/Baku")

    offset = datetime(
        2026,
        10,
        9,
        12,
        tzinfo=baku,
    ).utcoffset()

    if offset != timedelta(hours=4):
        raise RuntimeError(
            "Unexpected Asia/Baku timezone offset"
        )

    for module in (
        "uvicorn",
        "fastapi",
        "psycopg",
        "dwh_api",
        "dwh",
        "run_request_dispatcher",
    ):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(
                "Required Python module missing: "
                + module
            )


def check_database() -> None:
    from dwh.config import get_dwh_config
    from dwh.db import connect

    with warnings.catch_warnings(
        record=True
    ) as captured:
        warnings.simplefilter("always")

        with connect(get_dwh_config()) as conn:
            conn.read_only = True

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_database(),
                        current_user,
                        current_setting(
                            'transaction_read_only'
                        ),
                        current_setting('TimeZone'),
                        current_timestamp
                    """
                )

                (
                    database,
                    user,
                    readonly,
                    pg_timezone,
                    timestamp,
                ) = cur.fetchone()

            conn.rollback()

    if (
        database,
        user,
        readonly,
        pg_timezone,
    ) != (
        "dwh",
        "dwh_etl",
        "on",
        "Asia/Baku",
    ):
        raise RuntimeError(
            "Unexpected database connection"
        )

    if str(timestamp.tzinfo) != "Asia/Baku":
        raise RuntimeError(
            "PostgreSQL timestamp timezone mismatch"
        )

    timezone_warnings = [
        item
        for item in captured
        if (
            "timezone" in str(item.message).lower()
            or "Asia/Baku" in str(item.message)
        )
    ]

    if timezone_warnings:
        raise RuntimeError(
            "Timezone warning detected"
        )


def port_available() -> bool:
    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    ) as sock:
        sock.settimeout(1.0)

        return (
            sock.connect_ex((HOST, PORT)) != 0
        )


def run_local() -> None:
    import uvicorn

    from run_request_dispatcher import (
        main as dispatcher_once,
    )

    if not port_available():
        raise RuntimeError(
            "Port 8000 is already occupied"
        )

    config = uvicorn.Config(
        "dwh_api.main:app",
        host=HOST,
        port=PORT,
        reload=False,
        workers=1,
        log_level="info",
    )

    server = uvicorn.Server(config)

    stop_requested = threading.Event()

    worker_error = []


    def dispatcher_thread() -> None:
        # Wait until the backend actually starts listening.
        # Do not process queue work if backend startup fails.
        while not stop_requested.is_set():
            if server.started:
                break

            if server.should_exit:
                return

            stop_requested.wait(0.2)

        if stop_requested.is_set():
            return

        log("")
        log("LOCAL DWH DISPATCHER STARTED")
        log(
            "poll interval: "
            + str(POLL_SECONDS)
            + " seconds"
        )

        try:
            while not stop_requested.is_set():
                try:
                    dispatcher_once(
                        quiet_empty=True,
                    )

                except KeyboardInterrupt:
                    break

                except Exception as exc:
                    log(
                        "["
                        + datetime.now(
                            timezone.utc
                        ).isoformat(
                            timespec="seconds"
                        )
                        + "] DISPATCHER ITERATION FAILED"
                    )

                    log(
                        type(exc).__name__
                        + ": "
                        + str(exc)
                    )

                stop_requested.wait(
                    POLL_SECONDS
                )

        except BaseException as exc:
            worker_error.append(exc)
            log("DISPATCHER THREAD STOPPED UNEXPECTEDLY")
            traceback.print_exc()

            # Shut down backend too if the dispatcher
            # thread suffers a fatal failure.
            server.should_exit = True

        finally:
            log("LOCAL DWH DISPATCHER STOPPED")


    worker = threading.Thread(
        target=dispatcher_thread,
        name="dwh-local-dispatcher",
        daemon=False,
    )

    log("")
    log("========================================")
    log("DWH LOCAL DEVELOPMENT")
    log("========================================")
    log(
        "Backend: http://127.0.0.1:8000"
    )
    log(
        "Dispatcher: same Python process"
    )
    log(
        "Polling: 5 seconds"
    )
    log(
        "Stop: Ctrl+C"
    )
    log("")

    worker.start()

    try:
        # Uvicorn runs on the main thread so it can
        # handle Ctrl+C and normal shutdown signals.
        server.run()

    finally:
        # No new queue requests will be claimed after
        # the current dispatcher iteration finishes.
        stop_requested.set()

        log("")
        log(
            "WAITING FOR DISPATCHER TO FINISH "
            "ITS CURRENT ITERATION..."
        )

        # Intentionally no forced timeout:
        # do not interrupt an ETL task in progress.
        worker.join()

        log("DISPATCHER THREAD JOINED")
        log("LOCAL DWH STOPPED")

    if worker_error:
        raise RuntimeError(
            "Dispatcher thread failed"
        )

    if not server.started:
        raise RuntimeError(
            "Backend did not start successfully"
        )


def main() -> None:
    mode = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "Check"
    )

    if mode not in ("Check", "Run"):
        raise RuntimeError(
            "Expected Check or Run mode"
        )

    loaded = load_env()

    log(
        "PASS: .env loaded ("
        + str(loaded)
        + " entries)"
    )

    configure_python()
    verify_environment()
    check_database()

    log("PASS: required DWH configuration available")
    log("PASS: Python modules available")
    log("PASS: read-only DWH connection verified")
    log("PASS: Asia/Baku works without warnings")
    log("PASS: no child-process creation required")

    if mode == "Check":
        log("")
        log(
            "=== LOCAL DWH SINGLE-PROCESS CHECK PASSED ==="
        )
        log(
            "BACKEND: dwh_api.main:app"
        )
        log(
            "ADDRESS: http://127.0.0.1:8000"
        )
        log(
            "DISPATCHER: separate thread"
        )
        log(
            "NO BACKEND OR DISPATCHER WAS STARTED"
        )
        return

    run_local()


if __name__ == "__main__":
    main()