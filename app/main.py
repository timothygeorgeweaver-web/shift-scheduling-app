"""Web application entry point."""

from fastapi import FastAPI, HTTPException

from app.database import DatabaseUnavailable, check_database

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth_routes import router as auth_router

app = FastAPI(title="Shift Scheduling App", version="0.1.0")


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, error):
    # Validation errors can contain submitted passwords: do not echo inputs.
    return JSONResponse(
        status_code=422,
        content={"detail": "Invalid request."},
        headers={"Cache-Control": "no-store"},
    )


app.include_router(auth_router)



@app.get("/api/v1/health")
def health() -> dict[str, str]:
    """Report application liveness; does not check database readiness."""
    return {"status": "ok", "application": "shift-scheduling-app"}


@app.get("/api/v1/ready")
def ready() -> dict[str, str]:
    """Report database connectivity without revealing connection details."""
    try:
        check_database()
    except DatabaseUnavailable:
        raise HTTPException(
            status_code=503, detail="Database is unavailable."
        ) from None
    return {"status": "ready", "database": "ok"}
