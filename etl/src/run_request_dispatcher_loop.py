from __future__ import annotations

import time
from datetime import datetime, timezone

from run_request_dispatcher import main as run_once


POLL_SECONDS = 5


def timestamp() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat(
        timespec="seconds"
    )


def main() -> None:
    print(
        "INTEGRATION DISPATCHER LOOP STARTED"
    )
    print(
        f"poll interval: {POLL_SECONDS} seconds"
    )
    print(
        "press Ctrl+C to stop"
    )
    print()

    while True:
        try:
            run_once(
                quiet_empty=True,
            )

        except KeyboardInterrupt:
            raise

        except BaseException as exc:
            print()
            print(
                f"[{timestamp()}] "
                "DISPATCHER ITERATION FAILED"
            )
            print(
                f"{type(exc).__name__}: {exc}"
            )
            print(
                "worker will continue checking the queue"
            )
            print()

        time.sleep(
            POLL_SECONDS
        )


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print()
        print(
            "INTEGRATION DISPATCHER LOOP STOPPED"
        )