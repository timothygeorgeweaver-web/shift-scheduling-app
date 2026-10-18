"""Cookie-based authentication APIs for the local web application."""

import hashlib
import hmac
from threading import Lock
from time import monotonic

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

from app import auth_store
from app.security import session_token_digest

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
COOKIE_NAME = "scheduling_session"
SESSION_SECONDS = 8 * 60 * 60


class LoginLimiter:
    """Bounded, process-local limit; shared hosting needs a shared limiter."""

    def __init__(self):
        self._entries = {}
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = monotonic()
        with self._lock:
            self._entries = {
                k: v for k, v in self._entries.items()
                if now - v[0] < 60
            }
            if key not in self._entries:
                if len(self._entries) >= 1024:
                    self._deny()
                self._entries[key] = (now, 0)
            started, count = self._entries[key]
            if count >= 10:
                self._deny()
            self._entries[key] = (started, count + 1)

    @staticmethod
    def _deny():
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Try again shortly.",
            headers={"Retry-After": "60"},
        )


limiter = LoginLimiter()


class LoginInput(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if (
        request.headers.get("sec-fetch-site") == "cross-site"
        or (origin is not None and origin != expected)
    ):
        raise HTTPException(status_code=403, detail="Request origin is invalid.")


def _token(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME, "")
    try:
        session_token_digest(token)
    except ValueError:
        raise HTTPException(
            status_code=401, detail="Sign-in is required."
        ) from None
    return token


def _csrf(token: str) -> str:
    # Domain separation: this value is not the session cookie or its DB digest.
    return hashlib.sha256(("csrf:" + token).encode("ascii")).hexdigest()


@router.post("/login")
def login(body: LoginInput, request: Request, response: Response):
    _check_origin(request)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(status_code=415, detail="JSON is required.")

    limiter.check(request.client.host if request.client else "unknown")
    token = auth_store.create_session(
        body.email, body.password.get_secret_value()
    )
    if token is None:
        raise HTTPException(status_code=401, detail="Email or password is invalid.")

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"status": "signed_in", "csrf_token": _csrf(token)}


@router.get("/me")
def me(request: Request, response: Response):
    token = _token(request)
    user = auth_store.identity(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign-in is required.")
    response.headers["Cache-Control"] = "no-store"
    return {**user, "csrf_token": _csrf(token)}


@router.post("/logout", status_code=204)
def logout(request: Request):
    _check_origin(request)
    token = _token(request)
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), _csrf(token).encode("ascii")):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")

    auth_store.revoke_session(token)
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
