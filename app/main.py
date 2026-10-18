"""Web application entry point."""

from fastapi import FastAPI, HTTPException

from app.database import DatabaseUnavailable, check_database

app = FastAPI(title="Shift Scheduling App", version="0.1.0")


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
