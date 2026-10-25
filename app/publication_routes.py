"""Staff publication review and atomic, explicitly confirmed publication."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, HTTPException
import psycopg
from psycopg.types.json import Jsonb
from datetime import date

from app.publication_review import (
    PublishWeek, PublicationRejected, review_publication,
    validate_publication_confirmation,
)
from app.publication_locks import publication_lock_plan
from app.shift_routes import fail
from app.staff_data import staff_connection
from app.staff_write_routes import mutation_token

router = APIRouter(prefix="/api/v1/staff", tags=["schedule publication"])


@router.post("/locations/{location_id}/schedule-weeks/{week_id}/publication-review")
def preview_publication(
    location_id: UUID, week_id: UUID, body: PublishWeek,
    response: Response, token: str = Depends(mutation_token),
):
    # READ COMMITTED mutation-capable connection is required for row locks.
    # The scope and projection write no records.
    with staff_connection(token, write=True) as conn:
        try:
            inputs = conn.execute(
                "SELECT scheduling.lock_publication_scope("
                "%s,%s,%s,%s,%s) AS result",
                (
                    location_id, week_id, body.expected_week_revision,
                    body.expected_location_revision,
                    body.expected_publication_number,
                ),
            ).fetchone()["result"]
            publication_lock_plan(
                date.fromisoformat(inputs["week"]["week_start_date"]),
                inputs["week"]["timezone"],
                inputs["week"]["business_day_start_minute"],
            )
            result = review_publication(inputs)
            response.headers["Cache-Control"] = "no-store"
            return {
                **result,
                "week_revision": inputs["week"]["revision"],
                "location_revision": inputs["location_revision"],
                "publication_number": inputs["publication_number"],
            }
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except psycopg.Error as error:
            fail(error)


@router.post(
    "/locations/{location_id}/schedule-weeks/{week_id}/publish",
    status_code=201,
)
def publish_week(
    location_id: UUID, week_id: UUID, body: PublishWeek,
    response: Response, token: str = Depends(mutation_token),
):
    with staff_connection(token, write=True) as conn:
        try:
            locked = conn.execute(
                """
                WITH scope AS MATERIALIZED (
                    SELECT scheduling.lock_publication_scope(
                        %s,%s,%s,%s,%s
                    ) AS inputs
                )
                SELECT inputs,md5(inputs::text) AS fingerprint FROM scope
                """,
                (
                    location_id, week_id, body.expected_week_revision,
                    body.expected_location_revision,
                    body.expected_publication_number,
                ),
            ).fetchone()
            inputs = locked["inputs"]
            publication_lock_plan(
                date.fromisoformat(inputs["week"]["week_start_date"]),
                inputs["week"]["timezone"],
                inputs["week"]["business_day_start_minute"],
            )
            review = review_publication(inputs)
            validate_publication_confirmation(
                body, review,
                week_revision=inputs["week"]["revision"],
                location_revision=inputs["location_revision"],
                publication_number=inputs["publication_number"],
            )
            result = conn.execute(
                """
                SELECT scheduling.commit_schedule_publication(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s
                ) AS result
                """,
                (
                    location_id, week_id, body.expected_week_revision,
                    body.expected_location_revision,
                    body.expected_publication_number,
                    body.confirm_publish, body.confirm_unfilled,
                    locked["fingerprint"], Jsonb(review),
                ),
            ).fetchone()["result"]
            response.headers["Cache-Control"] = "no-store"
            return result
        except PublicationRejected as error:
            raise HTTPException(
                status_code=409 if error.code == "stale" else 422,
                detail=str(error),
            ) from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except psycopg.Error as error:
            fail(error)
