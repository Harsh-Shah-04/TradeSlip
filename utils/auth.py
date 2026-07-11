from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Response

from utils.supabase_client import get_broker_by_id, upsert_broker_row

ACCESS_COOKIE_NAME = "broker_access_token"
REFRESH_COOKIE_NAME = "broker_refresh_token"
REFRESH_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 8
HTTP_TIMEOUT = 20.0

logger = logging.getLogger(__name__)
_failed_logins: dict[str, deque[float]] = defaultdict(deque)


@dataclass(frozen=True)
class BrokerSession:
    id: str
    email: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "broker_id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "is_admin": self.is_admin,
        }


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


def get_bootstrap_admin_email() -> str | None:
    """First admin auto-provision email (also accepts legacy ALLOWED_EMAIL)."""
    raw = _env("ADMIN_BOOTSTRAP_EMAIL", "ALLOWED_EMAIL", "ALLOWED_EMAILS")
    if not raw:
        return None
    return raw.split(",")[0].strip().lower() or None


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


def _auth_headers(bearer: str | None = None) -> dict[str, str]:
    anon = _anon_key()
    token = bearer or anon
    return {
        "apikey": anon,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _broker_session_from_row(row: dict[str, Any]) -> BrokerSession | None:
    if not row.get("is_active", True):
        return None
    broker_id = str(row.get("id") or "")
    email = str(row.get("email") or "").strip().lower()
    if not broker_id or not email:
        return None
    role = str(row.get("role") or "broker")
    if role not in ("admin", "broker"):
        role = "broker"
    display_name = str(row.get("display_name") or email.split("@")[0])
    return BrokerSession(
        id=broker_id,
        email=email,
        display_name=display_name,
        role=role,
    )


def resolve_broker_for_auth_user(user: dict[str, Any]) -> BrokerSession:
    """Map a Supabase Auth user to an active brokers row (bootstrap admin if needed)."""
    user_id = str(user.get("id") or "")
    email = str(user.get("email") or "").strip().lower()
    if not user_id or not email:
        raise PermissionError("Invalid authenticated user.")

    row = get_broker_by_id(user_id)
    if row is not None:
        session = _broker_session_from_row(row)
        if session is None:
            raise PermissionError("This broker account has been deactivated. Contact the admin.")
        return session

    bootstrap = get_bootstrap_admin_email()
    if bootstrap and email == bootstrap:
        meta = user.get("user_metadata") if isinstance(user.get("user_metadata"), dict) else {}
        display_name = str(meta.get("display_name") or email.split("@")[0])
        created = upsert_broker_row(
            broker_id=user_id,
            email=email,
            display_name=display_name,
            role="admin",
            is_active=True,
        )
        session = _broker_session_from_row(created)
        if session is None:
            raise PermissionError("Could not bootstrap admin broker account.")
        logger.info("Bootstrapped admin broker for %s", email)
        return session

    raise PermissionError(
        "This account is not registered as a broker. Ask the admin to invite you."
    )


def sign_in_with_password(email: str, password: str) -> tuple[str, str, int, BrokerSession]:
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

    if not access_token or not refresh_token:
        raise PermissionError("Supabase did not return a valid session.")

    if not isinstance(user, dict):
        raise PermissionError("Supabase did not return a user.")

    broker = resolve_broker_for_auth_user(user)
    return access_token, refresh_token, expires_in, broker


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
    if not access_token:
        return None
    return access_token, new_refresh, expires_in


def resolve_authenticated_broker(
    access_token: str | None,
    refresh_token: str | None,
    response: Response | None = None,
) -> BrokerSession | None:
    user = get_user_from_access_token(access_token or "")
    if user is None:
        refreshed = refresh_session_tokens(refresh_token or "")
        if refreshed is None:
            return None
        new_access, new_refresh, expires_in = refreshed
        user = get_user_from_access_token(new_access)
        if user is None:
            return None
        if response is not None:
            set_auth_cookies(response, new_access, new_refresh, expires_in)
        access_token = new_access

    try:
        return resolve_broker_for_auth_user(user)
    except PermissionError:
        return None
    except Exception:
        logger.exception("Failed to resolve broker for authenticated user")
        return None


# Back-compat alias used by older call sites during refactor
def resolve_authenticated_user(
    access_token: str | None,
    refresh_token: str | None,
    response: Response | None = None,
) -> dict[str, Any] | None:
    broker = resolve_authenticated_broker(access_token, refresh_token, response)
    if broker is None:
        return None
    return {
        "id": broker.id,
        "email": broker.email,
        "display_name": broker.display_name,
        "role": broker.role,
    }


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
