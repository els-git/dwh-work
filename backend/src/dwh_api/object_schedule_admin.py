"""Schedules of source objects (I09, package 032).

GET /api/admin/integrations/object-schedules                     every active object with its schedule, last five
                                                                  requests (manual / scheduled / retry) and last five
                                                                  events of the scheduler (tab "Schedules")
PUT /api/admin/integrations/object-schedules/{source_object_id}  create or change the schedule of an object,
                                                                  enable or disable it

Both go through app.object_schedule_list / app.object_schedule_save: the database checks the session and the right
to run objects (as for manual runs), validates the schedule and writes the audit trail. Saving never starts a run:
the dispatcher creates the requests when the time comes (etl.object_schedule_fire).
"""

from __future__ import annotations

from typing import Any, Literal

import psycopg
from fastapi import APIRouter, HTTPException, Path as PathParam, Request
from pydantic import BaseModel, Field

from dwh_api import object_run_admin
from dwh_api.integration_admin import _db_message, _raise_database_error

router = APIRouter(prefix="/api/admin/integrations/object-schedules", tags=["Data loads"])

# Messages of app.object_schedule_save that describe the input or the object, not a server failure.
_REJECTED = ("invalid schedule", "source object not found", "no published ready configuration")


class ObjectScheduleSave(BaseModel):
    enabled: bool
    frequency: Literal["EVERY_MINUTES", "EVERY_HOURS", "DAILY", "WEEKLY"]
    every_n: int | None = Field(default=None, ge=1, le=720)
    at_time: str | None = Field(default=None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    week_days: list[int] | None = Field(default=None, max_length=7)
    run_kind: Literal["RUN", "FULL_RERUN"] = "RUN"
    retry_count: int = Field(default=0, ge=0, le=3)
    retry_delay_minutes: int = Field(default=15, ge=5, le=60)


def _raise_schedule_error(exc: psycopg.Error) -> None:
    message = _db_message(exc)
    if any(text in message.lower() for text in _REJECTED):
        raise HTTPException(status_code=422, detail=message) from exc
    _raise_database_error(exc)


@router.get("")
def list_object_schedules(request: Request) -> dict[str, Any]:
    return object_run_admin._call(request, "SELECT app.object_schedule_list(%s)", ())


@router.put("/{source_object_id}")
def save_object_schedule(payload: ObjectScheduleSave, request: Request,
                         source_object_id: int = PathParam(ge=1)) -> dict[str, Any]:
    return object_run_admin._call(
        request, "SELECT app.object_schedule_save(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (source_object_id, payload.enabled, payload.frequency, payload.every_n, payload.at_time,
         payload.week_days, payload.run_kind, payload.retry_count, payload.retry_delay_minutes),
        raise_error=_raise_schedule_error)
