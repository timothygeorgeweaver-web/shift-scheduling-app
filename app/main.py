"""Web application entry point."""

from fastapi import FastAPI

app = FastAPI(title="Shift Scheduling App", version="0.1.0")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    """Report application liveness; does not check database readiness."""
    return {"status": "ok", "application": "shift-scheduling-app"}
