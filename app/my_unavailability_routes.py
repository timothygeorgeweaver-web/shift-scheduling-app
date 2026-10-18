"""Private, read-only employee unavailability projections."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
import psycopg

from app.auth_routes import _token
from app.auth_store import connection, identity
from app.availability_calendar import CalendarPolicy, availability_gate
from app.security import session_token_digest
from app.unavailability_resolver import BlockWindow, blocked_for_date

router = APIRouter(prefix="/api/v1/me/unavailability", tags=["my unavailability"])


def private_read(token, statement, parameters=()):
    if identity(token) is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    with connection() as conn:
        conn.read_only = True
        conn.execute(
            "SELECT set_config('scheduling.session_sha256',%s,true)",
            (session_token_digest(token),),
        )
        try:
            return conn.execute(statement, parameters).fetchone()[0]
        except psycopg.Error as error:
            if error.sqlstate == "42501":
                raise HTTPException(
                    status_code=404, detail="Employee profile was not found."
                ) from None
            if error.sqlstate == "22023":
                raise HTTPException(
                    status_code=422, detail="Invalid date range."
                ) from None
            raise


@router.get("/profiles")
def profiles(token: str = Depends(_token)):
    return private_read(token, "SELECT scheduling.my_unavailability_profiles()")


@router.get("/{location_id}")
def calendar(
    location_id: UUID,
    start: date,
    days: int = Query(default=7, ge=1, le=35),
    token: str = Depends(_token),
):
    if start > date.max-timedelta(days=days-1):
        raise HTTPException(status_code=422, detail="Invalid date range.")
    data = private_read(
        token, "SELECT scheduling.read_my_unavailability(%s,%s,%s)",
        (location_id, start, days),
    )
    recurring = {
        date.fromisoformat(row["origin_date"]): [
            BlockWindow(**window) for window in row["windows"]
        ] for row in data["recurring"]
    }
    exceptions = {
        date.fromisoformat(row["date"]): [
            BlockWindow(**window) for window in row["windows"]
        ] for row in data["exceptions"] if row["present"]
    }
    locks = {row["date"]: row for row in data["locks"]}
    profile = data["profile"]
    policy = CalendarPolicy(
        timezone=profile["timezone"], week_start=profile["week_start"],
        cutoff_days_before=profile["cutoff_days_before"],
        cutoff_time=time.fromisoformat(profile["cutoff_time"]),
    )
    now = datetime.now(timezone.utc)
    effective = []
    for offset in range(days):
        day = start+timedelta(days=offset)
        frozen = locks.get(day.isoformat())
        blocked = (
            tuple(tuple(window) for window in frozen["blocked"])
            if frozen else blocked_for_date(
                day, lambda origin: recurring.get(origin, ()), exceptions
            )
        )
        available = []
        cursor = 0
        for beginning, end in blocked:
            if cursor < beginning:
                available.append((cursor, beginning))
            cursor = end
        if cursor < 1440:
            available.append((cursor, 1440))

        try:
            gate = availability_gate(day, now, policy)
            editable = gate.editable
            reason = gate.reason
            cutoff = gate.cutoff
        except ValueError:
            editable, reason, cutoff = False, "invalid_cutoff_time", None
        if frozen:
            editable = False
            reason = (
                "published_week" if frozen["reason"] == "publication"
                else "cutoff_reached"
            )
        effective.append({
            "date": day, "blocked": blocked, "available": available,
            "source": (
                "frozen" if frozen else
                "exception" if day in exceptions else "recurring"
            ),
            "editable": editable, "lock_reason": reason, "cutoff": cutoff,
        })
    data["effective_days"] = effective
    return data
