from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from typing import Any

import httpx
from fastapi import Response

ACCESS_COOKIE_NAME = "broker_access_token"
REFRESH_COOKIE_NAME = "broker_refresh_token"
REFRESH_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 8
HTTP_TIMEOUT = 20.0

logger = logging.getLogger(__name__)
_failed_logins: dict[str, deque[float]] = defaultdict(deque)


def _env(name: str, *aliases: str) -> str:
    for key in (name, *aliases):
        value = os.environ.get(key, "").strip().strip('"').strip("'")
        if value:
            return value
    return ""


def _is_production() -> bool:
    return (
        os.environ.get("VERCEL", "").strip() == "1"
        or _env("ENVIRONMENT").lower() == "production"
    )


def cookie_secure_enabled() -> bool:
    explicit = _env("COOKIE_SECURE").lower()
    if explicit in ("true", "1", "yes"):
        return True
    if explicit in ("false", "0", "no"):
        return False
    return _is_production()


def get_allowed_emails() -> set[str]:
    raw = _env("ALLOWED_EMAIL", "ALLOWED_EMAILS")
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _supabase_url() -> str:
    return _env("SUPABASE_URL").rstrip("/")


def _anon_key() -> str:
    return _env("SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY")


def validate_auth_config() -> None:
    url = _supabase_url()
    service_key = _env("SUPABASE_KEY")
    anon_key = _anon_key()
    if not url or not service_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in the environment.")
    if not anon_key:
        raise RuntimeError(
            "SUPABASE_ANON_KEY must be set (Supabase → Project Settings → API → anon public)."
        )
    if not get_allowed_emails():
        raise RuntimeError(
            "ALLOWED_EMAIL must be set to the Supabase Auth user email that may access "
            "this dashboard."
        )


def is_email_allowed(email: str | None) -> bool:
    if not email:
        return False
    return email.strip().lower() in get_allowed_emails()


def _auth_headers(bearer: str | None = None) -> dict[str, str]:
    anon = _anon_key()
    token = bearer or anon
    return {
        "apikey": anon,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def sign_in_with_password(email: str, password: str) -> tuple[str, str, int]:
    """Authenticate against Supabase Auth via HTTP (reliable on Vercel)."""
    normalized_email = email.strip().lower()
    url = f"{_supabase_url()}/auth/v1/token?grant_type=password"
    try:
        response = httpx.post(
            url,
            headers=_auth_headers(),
            json={"email": normalized_email, "password": password},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        logger.exception("Supabase login request failed")
        raise PermissionError(f"Could not reach Supabase Auth: {exc}") from exc

    if response.status_code != 200:
        payload: dict[str, Any] = {}
        try:
            payload = response.json()
        except Exception:
            pass
        message = (
            str(payload.get("msg") or payload.get("error_description") or payload.get("error") or "")
            .strip()
        )
        logger.warning(
            "Supabase login rejected for %s status=%s body=%s",
            normalized_email,
            response.status_code,
            response.text[:300],
        )
        lowered = message.lower()
        if "confirm" in lowered:
            raise PermissionError(
                "Email is not confirmed in Supabase. Disable Confirm email, then try again."
            )
        if message:
            raise PermissionError(message)
        raise PermissionError("Invalid email or password.")

    data = response.json()
    access_token = str(data.get("access_token") or "")
    refresh_token = str(data.get("refresh_token") or "")
    expires_in = int(data.get("expires_in") or 3600)
    user = data.get("user") or {}
    email_value = str(user.get("email") or normalized_email)

    if not access_token or not refresh_token:
        raise PermissionError("Supabase did not return a valid session.")

    if not is_email_allowed(email_value):
        raise PermissionError(
            f"Signed in as {email_value!r}, but ALLOWED_EMAIL does not include this address."
        )

    return access_token, refresh_token, expires_in


def get_user_from_access_token(access_token: str) -> dict[str, Any] | None:
    if not access_token:
        return None
    try:
        response = httpx.get(
            f"{_supabase_url()}/auth/v1/user",
            headers=_auth_headers(access_token),
            timeout=HTTP_TIMEOUT,
        )
    except Exception:
        return None
    if response.status_code != 200:
        return None
    user = response.json()
    email = user.get("email") if isinstance(user, dict) else None
    if not is_email_allowed(str(email) if email else None):
        return None
    return user if isinstance(user, dict) else None


def refresh_session_tokens(refresh_token: str) -> tuple[str, str, int] | None:
    if not refresh_token:
        return None
    try:
        response = httpx.post(
            f"{_supabase_url()}/auth/v1/token?grant_type=refresh_token",
            headers=_auth_headers(),
            json={"refresh_token": refresh_token},
            timeout=HTTP_TIMEOUT,
        )
    except Exception:
        return None
    if response.status_code != 200:
        return None
    data = response.json()
    access_token = str(data.get("access_token") or "")
    new_refresh = str(data.get("refresh_token") or refresh_token)
    expires_in = int(data.get("expires_in") or 3600)
    user = data.get("user") or {}
    email = user.get("email") if isinstance(user, dict) else None
    if email is not None and not is_email_allowed(str(email)):
        return None
    if not access_token:
        return None
    return access_token, new_refresh, expires_in


def resolve_authenticated_user(
    access_token: str | None,
    refresh_token: str | None,
    response: Response | None = None,
) -> dict[str, Any] | None:
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
    if not access_token:
        return
    try:
        httpx.post(
            f"{_supabase_url()}/auth/v1/logout",
            headers=_auth_headers(access_token),
            timeout=HTTP_TIMEOUT,
        )
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
