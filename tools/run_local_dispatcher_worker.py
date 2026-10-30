from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from run_request_dispatcher import main as run_once


POLL_SECONDS = 5


def timestamp() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat(
        timespec="seconds"
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError(
            "Expected one stop-file argument"
        )

    stop_file = Path(
        sys.argv[1]
    ).resolve()

    print(
        "LOCAL DWH DISPATCHER STARTED",
        flush=True,
    )
    print(
        f"poll interval: {POLL_SECONDS} seconds",
        flush=True,
    )

    while not stop_file.exists():
        try:
            # Uses the real integration dispatcher.
            # An executing ETL task is allowed to finish.
            run_once(
                quiet_empty=True,
            )

        except KeyboardInterrupt:
            break

        except BaseException as exc:
            print(
                f"[{timestamp()}] "
                "DISPATCHER ITERATION FAILED",
                flush=True,
            )
            print(
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

        # Check shutdown every 250 ms while idle,
        # without changing the normal 5-second polling.
        for _ in range(
            POLL_SECONDS * 4
        ):
            if stop_file.exists():
                break

            time.sleep(0.25)

    print(
        "LOCAL DWH DISPATCHER STOPPED",
        flush=True,
    )


if __name__ == "__main__":
    main()