"""CSRF protection helpers."""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256

from fastapi import HTTPException, Request, Response, status

from backend.core.config import get_settings

CSRF_COOKIE_NAME = "tg-signer-csrf"
CSRF_HEADER_NAME = "x-csrf-token"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
}


def _sign_token(raw_token: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        raw_token.encode("utf-8"),
        sha256,
    ).hexdigest()


def create_csrf_token() -> str:
    raw_token = secrets.token_urlsafe(32)
    return f"{raw_token}.{_sign_token(raw_token)}"


def is_valid_csrf_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    raw_token, signature = token.rsplit(".", 1)
    if not raw_token or not signature:
        return False
    return hmac.compare_digest(signature, _sign_token(raw_token))


def set_csrf_cookie(response: Response) -> str:
    settings = get_settings()
    token = create_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
        path="/",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
    )
    return token


def clear_csrf_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=settings.refresh_cookie_secure,
        samesite=settings.refresh_cookie_samesite,
    )


def enforce_csrf(request: Request) -> None:
    if request.method.upper() in CSRF_SAFE_METHODS:
        return
    if request.url.path in CSRF_EXEMPT_PATHS:
        return
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if (
        not cookie_token
        or not header_token
        or not hmac.compare_digest(cookie_token, header_token)
        or not is_valid_csrf_token(cookie_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing or invalid",
        )
