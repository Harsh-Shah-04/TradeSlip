from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Response

from utils.supabase_client import get_supabase

ACCESS_COOKIE_NAME = "broker_access_token"
REFRESH_COOKIE_NAME = "broker_refresh_token"
# Keep refresh cookie for a week; access token lifetime comes from Supabase.
REFRESH_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5

_failed_logins: dict[str, deque[float]] = defaultdict(deque)


def _is_production() -> bool:
    return (
        os.environ.get("VERCEL", "").strip() == "1"
        or os.environ.get("ENVIRONMENT", "").strip().lower() == "production"
    )


def cookie_secure_enabled() -> bool:
    explicit = os.environ.get("COOKIE_SECURE", "").strip().lower()
    if explicit in ("true", "1", "yes"):
        return True
    if explicit in ("false", "0", "no"):
        return False
    return _is_production()


def get_allowed_emails() -> set[str]:
    raw = os.environ.get("ALLOWED_EMAIL", os.environ.get("ALLOWED_EMAILS", "")).strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def validate_auth_config() -> None:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in the environment.")
    if not get_allowed_emails():
        raise RuntimeError(
            "ALLOWED_EMAIL must be set to the Supabase Auth user email that may access "
            "this dashboard (example: father@example.com)."
        )


def is_email_allowed(email: str | None) -> bool:
    if not email:
        return False
    return email.strip().lower() in get_allowed_emails()


def _user_email(user: Any) -> str | None:
    if user is None:
        return None
    email = getattr(user, "email", None)
    if email:
        return str(email)
    if isinstance(user, dict):
        value = user.get("email")
        return str(value) if value else None
    return None


def sign_in_with_password(email: str, password: str) -> tuple[str, str, int]:
    """Authenticate against Supabase Auth. Returns access_token, refresh_token, expires_in."""
    client = get_supabase()
    try:
        result = client.auth.sign_in_with_password(
            {"email": email.strip(), "password": password}
        )
    except Exception as exc:
        raise PermissionError("Invalid email or password.") from exc

    session = getattr(result, "session", None)
    user = getattr(result, "user", None)
    if session is None or user is None:
        raise PermissionError("Invalid email or password.")

    email_value = _user_email(user)
    if not is_email_allowed(email_value):
        try:
            client.auth.sign_out()
        except Exception:
            pass
        raise PermissionError("This account is not allowed to access the dashboard.")

    access_token = getattr(session, "access_token", None) or ""
    refresh_token = getattr(session, "refresh_token", None) or ""
    expires_in = int(getattr(session, "expires_in", None) or 3600)
    if not access_token or not refresh_token:
        raise PermissionError("Supabase did not return a valid session.")
    return access_token, refresh_token, expires_in


def get_user_from_access_token(access_token: str) -> Any | None:
    if not access_token:
        return None
    client = get_supabase()
    try:
        result = client.auth.get_user(access_token)
    except Exception:
        return None
    user = getattr(result, "user", None)
    if user is None:
        return None
    if not is_email_allowed(_user_email(user)):
        return None
    return user


def refresh_session_tokens(refresh_token: str) -> tuple[str, str, int] | None:
    if not refresh_token:
        return None
    client = get_supabase()
    try:
        result = client.auth.refresh_session(refresh_token)
    except Exception:
        return None

    session = getattr(result, "session", None)
    user = getattr(result, "user", None)
    if session is None:
        return None
    if user is not None and not is_email_allowed(_user_email(user)):
        return None

    access_token = getattr(session, "access_token", None) or ""
    new_refresh = getattr(session, "refresh_token", None) or refresh_token
    expires_in = int(getattr(session, "expires_in", None) or 3600)
    if not access_token:
        return None
    return access_token, new_refresh, expires_in


def resolve_authenticated_user(
    access_token: str | None,
    refresh_token: str | None,
    response: Response | None = None,
) -> Any | None:
    user = get_user_from_access_token(access_token or "")
    if user is not None:
        return user

    refreshed = refresh_session_tokens(refresh_token or "")
    if refreshed is None:
        return None

    new_access, new_refresh, expires_in = refreshed
    user = get_user_from_access_token(new_access)
    if user is None:
        return None

    if response is not None:
        set_auth_cookies(response, new_access, new_refresh, expires_in)
    return user


def set_auth_cookies(
    response: Response,
    access_token: str,
    refresh_token: str,
    expires_in: int,
) -> None:
    secure = cookie_secure_enabled()
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_token,
        max_age=max(60, int(expires_in)),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=REFRESH_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    secure = cookie_secure_enabled()
    for key in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
        response.delete_cookie(
            key=key,
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/",
        )


def sign_out_supabase(access_token: str | None, refresh_token: str | None) -> None:
    if not access_token and not refresh_token:
        return
    client = get_supabase()
    try:
        if access_token and refresh_token:
            client.auth.set_session(access_token, refresh_token)
        client.auth.sign_out()
    except Exception:
        pass


def _prune_failures(client_key: str, now: float) -> deque[float]:
    bucket = _failed_logins[client_key]
    cutoff = now - LOGIN_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    return bucket


def is_login_rate_limited(client_key: str) -> bool:
    now = time.time()
    bucket = _prune_failures(client_key or "unknown", now)
    return len(bucket) >= LOGIN_MAX_ATTEMPTS


def record_failed_login(client_key: str) -> None:
    now = time.time()
    bucket = _prune_failures(client_key or "unknown", now)
    bucket.append(now)


def clear_failed_logins(client_key: str) -> None:
    _failed_logins.pop(client_key or "unknown", None)
