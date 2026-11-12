"""DWH dispatcher as a systemd service on the Linux server (I16).

The same loop as the dispatcher thread of tools/run_local_dwh_threaded.py on the PC:
every POLL_SECONDS one call of run_request_dispatcher.main() (a CRM request, or an
object request when the CRM queue is empty; object runs go to child processes).
SIGTERM (systemctl stop) lets the current iteration finish, then the loop ends.
"""
from __future__ import annotations

import signal
import sys
import threading
import traceback
from datetime import datetime, timezone

POLL_SECONDS = 5


def log(message: str) -> None:
    print(message, flush=True)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def serve(run_once, stop: threading.Event, poll_seconds: float = POLL_SECONDS) -> None:
    log("DWH DISPATCHER STARTED")
    log(f"poll interval: {poll_seconds} seconds")
    while not stop.is_set():
        try:
            run_once(quiet_empty=True)
        except Exception as exc:  # noqa: BLE001 - one failed iteration must not stop the service
            log(f"[{stamp()}] DISPATCHER ITERATION FAILED")
            log(f"{type(exc).__name__}: {exc}")
        stop.wait(poll_seconds)
    log("DWH DISPATCHER STOPPED")


def main() -> int:
    from run_request_dispatcher import main as run_once

    stop = threading.Event()

    def request_stop(signum, frame) -> None:  # noqa: ARG001
        log(f"[{stamp()}] stop requested; the current iteration finishes first")
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        serve(run_once, stop)
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
