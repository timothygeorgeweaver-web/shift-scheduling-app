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


from fastapi.staticfiles import StaticFiles
from app.ui_routes import STATIC_DIR, router as ui_router

app.include_router(auth_router)
app.include_router(ui_router)

from app.staff_routes import router as staff_router
app.include_router(staff_router)

from app.staff_write_routes import router as staff_write_router
app.include_router(staff_write_router)
from app.account_routes import router as account_router
app.include_router(account_router)
from app.settings_routes import router as settings_router
app.include_router(settings_router)
from app.availability_policy_routes import router as availability_policy_router
app.include_router(availability_policy_router)
from app.my_unavailability_routes import router as my_unavailability_router
app.include_router(my_unavailability_router)
from app.unavailability_write_routes import router as unavailability_write_router
app.include_router(unavailability_write_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def response_security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith(("/api/v1/auth", "/api/v1/staff", "/api/v1/me")):
        response.headers["Cache-Control"] = "no-store"
    return response




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
