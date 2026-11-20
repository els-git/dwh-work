"""I02-03 step 5: probe of the ETL password read, run by dwh_secret_accept.py etl-deny inside a short
systemd unit as the user dwh (the same settings and master keys as the dispatcher, see dwh_task.py).

  DWH_PROBE_SOURCE=<code> DWH_PROBE_FINISHED_REQUEST=<id> python secret_read_probe.py

Three reads of the source password by the ETL code (etl/src/dwh/source_secret.py), each must be refused:
  no_request        - no load request id (as a process started by hand): refused before the database
  unknown_request   - request id -1: the database refuses (REQUEST_NOT_FOUND), a DENIED READ is journaled
  finished_request  - a finished request of this source: the database refuses (REQUEST_NOT_RUNNING), DENIED
Prints one line per read: "PROBE <case>: REFUSED <code>" or "PROBE <case>: GIVEN" (the value is never
printed). Exit code 0 when all three were refused.
"""
from __future__ import annotations

import os
import sys

from dwh.source_secret import REQUEST_ENV, SourceSecretError, read_source_credential


def refusal_code(message: str) -> str:
    """Last word of a refusal message: the database code (REQUEST_NOT_RUNNING ...) or the reason."""
    if REQUEST_ENV in message:
        return "NO_LOAD_REQUEST"
    tail = message.rsplit(":", 1)[-1].strip()
    return tail.split()[0] if tail and tail.split()[0].isupper() else tail[:80]


def probe(source: str, finished_request: int, reader=read_source_credential) -> list[tuple[str, str]]:
    os.environ.pop(REQUEST_ENV, None)
    results = []
    for case, request_id in (("no_request", None), ("unknown_request", -1), ("finished_request", finished_request)):
        try:
            value = reader(source, request_id=request_id)
        except SourceSecretError as exc:
            results.append((case, "REFUSED " + refusal_code(str(exc))))
        else:
            del value
            results.append((case, "GIVEN"))
    return results


def main() -> int:
    source = os.environ.get("DWH_PROBE_SOURCE", "").strip()
    finished = os.environ.get("DWH_PROBE_FINISHED_REQUEST", "").strip()
    if not source or not finished.isdigit():
        print("PROBE STOP: DWH_PROBE_SOURCE and DWH_PROBE_FINISHED_REQUEST are required")
        return 2
    results = probe(source, int(finished))
    for case, outcome in results:
        print(f"PROBE {case}: {outcome}", flush=True)
    return 0 if all(outcome.startswith("REFUSED") for _, outcome in results) else 1


if __name__ == "__main__":
    sys.exit(main())
