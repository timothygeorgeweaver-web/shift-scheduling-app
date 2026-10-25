"""Staff publication review; no publication mutation is enabled yet."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response
import psycopg

from app.publication_review import PublishWeek, review_publication
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
            result = review_publication(inputs)
            response.headers["Cache-Control"] = "no-store"
            return {
                **result,
                "week_revision": inputs["week"]["revision"],
                "location_revision": inputs["location_revision"],
                "publication_number": inputs["publication_number"],
            }
        except psycopg.Error as error:
            fail(error)
