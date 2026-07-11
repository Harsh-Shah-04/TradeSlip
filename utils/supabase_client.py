from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from utils.config import DEFAULT_TEMPLATE_PATH
from utils.pdf_processor import storage_path_for

BUCKET_NAME = "trade-slips"
TABLE_NAME = "daily_trade_slips"
BROKERS_TABLE = "brokers"
SIGNED_URL_EXPIRES_SECONDS = 600
DEFAULT_TEMPLATE_STORAGE_PATH = "templates/blank-trade-slip.pdf"
HTTP_TIMEOUT = 30.0

_cached_template_path: Path | None = None


def _env(name: str, *aliases: str) -> str:
    for key in (name, *aliases):
        value = os.environ.get(key, "").strip().strip('"').strip("'")
        if value:
            return value
    return ""


def _supabase_url() -> str:
    url = _env("SUPABASE_URL")
    if not url:
        raise RuntimeError("SUPABASE_URL must be set in the environment.")
    return url.rstrip("/")


def _service_key() -> str:
    key = _env("SUPABASE_KEY")
    if not key:
        raise RuntimeError("SUPABASE_KEY must be set in the environment.")
    return key


def _service_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    key = _service_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    if extra:
        headers.update(extra)
    return headers


def upload_slip(path: str, pdf_bytes: bytes, upsert: bool) -> str:
    response = httpx.post(
        f"{_supabase_url()}/storage/v1/object/{BUCKET_NAME}/{path.lstrip('/')}",
        headers=_service_headers(
            {
                "Content-Type": "application/pdf",
                "x-upsert": "true" if upsert else "false",
            }
        ),
        content=pdf_bytes,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Storage upload failed ({response.status_code}): {response.text[:300]}")
    return path


def create_signed_slip_url(storage_path: str, expires_in: int = SIGNED_URL_EXPIRES_SECONDS) -> str:
    response = httpx.post(
        f"{_supabase_url()}/storage/v1/object/sign/{BUCKET_NAME}/{storage_path.lstrip('/')}",
        headers=_service_headers({"Content-Type": "application/json"}),
        json={"expiresIn": expires_in},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Signed URL failed ({response.status_code}): {response.text[:300]}"
        )
    payload = response.json()
    signed_path = payload.get("signedURL") or payload.get("signedUrl") or ""
    if not signed_path:
        raise RuntimeError(f"Supabase did not return a signed URL for {storage_path!r}.")
    if signed_path.startswith("http"):
        return signed_path
    return f"{_supabase_url()}/storage/v1{signed_path}"


def upsert_slip_row(
    broker_id: str,
    client_code: str,
    client_name: str,
    trade_date_iso: str,
    public_url: str,
    status: str = "Unsigned",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "broker_id": broker_id,
        "client_code": client_code,
        "client_name": client_name,
        "trade_date": trade_date_iso,
        "status": status,
        "public_url": public_url,
        "updated_at": now,
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{TABLE_NAME}",
        headers=_service_headers(
            {
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            }
        ),
        params={"on_conflict": "broker_id,client_code,trade_date"},
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Upsert failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    return row


def mark_signed(
    broker_id: str,
    client_code: str,
    trade_date_iso: str,
    public_url: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{TABLE_NAME}",
        headers=_service_headers(
            {
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
        ),
        params={
            "broker_id": f"eq.{broker_id}",
            "client_code": f"eq.{client_code}",
            "trade_date": f"eq.{trade_date_iso}",
        },
        json={
            "status": "Signed",
            "public_url": public_url,
            "updated_at": now,
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Mark signed failed ({response.status_code}): {response.text[:300]}")
    data = response.json() if response.content else []
    if isinstance(data, list) and data:
        return data[0]
    raise LookupError(
        f"No slip record for client {client_code!r} on trade date {trade_date_iso}."
    )


def list_slips(
    broker_id: str,
    trade_date_iso: str,
    status: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "broker_id": f"eq.{broker_id}",
        "trade_date": f"eq.{trade_date_iso}",
        "order": "client_code",
    }
    if status:
        params["status"] = f"eq.{status}"
    if search and search.strip():
        params["client_code"] = f"ilike.*{search.strip()}*"

    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{TABLE_NAME}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List slips failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    return data if isinstance(data, list) else []


def get_slip_row(broker_id: str, client_code: str, trade_date_iso: str) -> dict[str, Any]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{TABLE_NAME}",
        headers=_service_headers(),
        params={
            "select": "*",
            "broker_id": f"eq.{broker_id}",
            "client_code": f"eq.{client_code}",
            "trade_date": f"eq.{trade_date_iso}",
            "limit": "1",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Fetch slip failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        return data[0]
    raise LookupError(
        f"No slip record for client {client_code!r} on trade date {trade_date_iso}."
    )


def update_slip_row(
    broker_id: str,
    client_code: str,
    trade_date_iso: str,
    *,
    client_name: str | None = None,
    public_url: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    patch: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if client_name is not None:
        patch["client_name"] = client_name
    if public_url is not None:
        patch["public_url"] = public_url
    if status is not None:
        patch["status"] = status

    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{TABLE_NAME}",
        headers=_service_headers(
            {
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
        ),
        params={
            "broker_id": f"eq.{broker_id}",
            "client_code": f"eq.{client_code}",
            "trade_date": f"eq.{trade_date_iso}",
        },
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Update slip failed ({response.status_code}): {response.text[:300]}")
    data = response.json() if response.content else []
    if isinstance(data, list) and data:
        return data[0]
    raise LookupError(
        f"No slip record for client {client_code!r} on trade date {trade_date_iso}."
    )


def delete_slip_row(broker_id: str, client_code: str, trade_date_iso: str) -> None:
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{TABLE_NAME}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={
            "broker_id": f"eq.{broker_id}",
            "client_code": f"eq.{client_code}",
            "trade_date": f"eq.{trade_date_iso}",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Delete slip row failed ({response.status_code}): {response.text[:300]}")


def delete_storage_object(storage_path: str) -> None:
    """Delete a storage object. Missing files (404) are ignored."""
    encoded = "/".join(quote(part, safe="") for part in storage_path.lstrip("/").split("/"))
    response = httpx.delete(
        f"{_supabase_url()}/storage/v1/object/{BUCKET_NAME}/{encoded}",
        headers=_service_headers(),
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code in (200, 204, 404):
        return
    remove = httpx.post(
        f"{_supabase_url()}/storage/v1/object/{BUCKET_NAME}/remove",
        headers=_service_headers({"Content-Type": "application/json"}),
        json={"prefixes": [storage_path.lstrip("/")]},
        timeout=HTTP_TIMEOUT,
    )
    if remove.status_code not in (200, 204):
        raise RuntimeError(
            f"Storage delete failed ({response.status_code}/{remove.status_code}): "
            f"{response.text[:200]} | {remove.text[:200]}"
        )


def resolve_storage_path(client_code: str, trade_date_iso: str, broker_id: str) -> str:
    return storage_path_for(client_code, trade_date_iso, broker_id=broker_id)


def download_slip_bytes(storage_path: str) -> bytes:
    encoded = "/".join(quote(part, safe="") for part in storage_path.lstrip("/").split("/"))
    response = httpx.get(
        f"{_supabase_url()}/storage/v1/object/{BUCKET_NAME}/{encoded}",
        headers=_service_headers(),
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Download failed ({response.status_code}) for {storage_path!r}: {response.text[:300]}"
        )
    return response.content


def resolve_blank_template_path(preferred: Path | None = None) -> Path:
    global _cached_template_path

    if preferred is not None and preferred.exists():
        return preferred

    env_local = _env("TEMPLATE_PATH")
    if env_local:
        local_path = Path(env_local)
        if not local_path.is_absolute():
            local_path = Path(__file__).resolve().parent.parent / local_path
        if local_path.exists():
            return local_path

    if DEFAULT_TEMPLATE_PATH.exists():
        return DEFAULT_TEMPLATE_PATH

    if _cached_template_path is not None and _cached_template_path.exists():
        return _cached_template_path

    storage_path = _env("TEMPLATE_STORAGE_PATH") or DEFAULT_TEMPLATE_STORAGE_PATH
    pdf_bytes = download_slip_bytes(storage_path)

    cache_dir = Path(tempfile.gettempdir()) / "tradeslip_templates"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / Path(storage_path).name
    cache_path.write_bytes(pdf_bytes)
    _cached_template_path = cache_path
    return cache_path


# ---------------------------------------------------------------------------
# Brokers
# ---------------------------------------------------------------------------


def get_broker_by_id(broker_id: str) -> dict[str, Any] | None:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{BROKERS_TABLE}",
        headers=_service_headers(),
        params={"select": "*", "id": f"eq.{broker_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Fetch broker failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        return data[0]
    return None


def get_broker_by_email(email: str) -> dict[str, Any] | None:
    normalized = email.strip().lower()
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{BROKERS_TABLE}",
        headers=_service_headers(),
        params={"select": "*", "email": f"eq.{normalized}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Fetch broker failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        return data[0]
    return None


def list_brokers() -> list[dict[str, Any]]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{BROKERS_TABLE}",
        headers=_service_headers(),
        params={"select": "*", "order": "created_at.asc"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List brokers failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    return data if isinstance(data, list) else []


def upsert_broker_row(
    broker_id: str,
    email: str,
    display_name: str,
    role: str = "broker",
    is_active: bool = True,
) -> dict[str, Any]:
    row = {
        "id": broker_id,
        "email": email.strip().lower(),
        "display_name": display_name.strip() or email.split("@")[0],
        "role": role,
        "is_active": is_active,
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{BROKERS_TABLE}",
        headers=_service_headers(
            {
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=representation",
            }
        ),
        params={"on_conflict": "id"},
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Upsert broker failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and data:
        return data
    return row


def set_broker_active(broker_id: str, is_active: bool) -> dict[str, Any]:
    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{BROKERS_TABLE}",
        headers=_service_headers(
            {
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
        ),
        params={"id": f"eq.{broker_id}"},
        json={"is_active": is_active},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Update broker failed ({response.status_code}): {response.text[:300]}"
        )
    data = response.json() if response.content else []
    if isinstance(data, list) and data:
        return data[0]
    raise LookupError(f"No broker with id {broker_id!r}.")


def update_broker_profile(broker_id: str, display_name: str) -> dict[str, Any]:
    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{BROKERS_TABLE}",
        headers=_service_headers(
            {
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
        ),
        params={"id": f"eq.{broker_id}"},
        json={"display_name": display_name.strip()},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Update broker profile failed ({response.status_code}): {response.text[:300]}"
        )
    data = response.json() if response.content else []
    if isinstance(data, list) and data:
        return data[0]
    raise LookupError(f"No broker with id {broker_id!r}.")


def create_auth_user(email: str, password: str, display_name: str) -> dict[str, Any]:
    """Create a Supabase Auth user via the Admin API (service role)."""
    response = httpx.post(
        f"{_supabase_url()}/auth/v1/admin/users",
        headers=_service_headers({"Content-Type": "application/json"}),
        json={
            "email": email.strip().lower(),
            "password": password,
            "email_confirm": True,
            "user_metadata": {"display_name": display_name},
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        payload: dict[str, Any] = {}
        try:
            payload = response.json()
        except Exception:
            pass
        message = str(
            payload.get("msg")
            or payload.get("message")
            or payload.get("error_description")
            or payload.get("error")
            or response.text[:300]
        )
        raise RuntimeError(f"Create auth user failed: {message}")
    data = response.json()
    if not isinstance(data, dict) or not data.get("id"):
        raise RuntimeError("Supabase Admin API did not return a user id.")
    return data


def change_user_password(access_token: str, new_password: str) -> None:
    anon = _env("SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY")
    response = httpx.put(
        f"{_supabase_url()}/auth/v1/user",
        headers={
            "apikey": anon,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"password": new_password},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        payload: dict[str, Any] = {}
        try:
            payload = response.json()
        except Exception:
            pass
        message = str(
            payload.get("msg")
            or payload.get("message")
            or payload.get("error_description")
            or "Could not change password."
        )
        raise RuntimeError(message)


def broker_owns_storage_path(broker_id: str, storage_path: str) -> bool:
    cleaned = storage_path.strip().lstrip("/")
    if cleaned.startswith(f"{broker_id}/"):
        return True
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{TABLE_NAME}",
        headers=_service_headers(),
        params={
            "select": "id",
            "broker_id": f"eq.{broker_id}",
            "public_url": f"eq.{cleaned}",
            "limit": "1",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        return False
    data = response.json()
    return isinstance(data, list) and bool(data)


def list_history_days(
    broker_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    unsigned_only: bool = False,
) -> list[dict[str, Any]]:
    """Return per-day summaries for a broker by aggregating slip rows."""
    params: dict[str, str] = {
        "select": "trade_date,status,updated_at",
        "broker_id": f"eq.{broker_id}",
        "order": "trade_date.desc",
        "limit": "10000",
    }
    if date_from:
        params["trade_date"] = f"gte.{date_from}"
    if date_to:
        # PostgREST: combine filters with and=(...)
        if date_from:
            params["and"] = f"(trade_date.gte.{date_from},trade_date.lte.{date_to})"
            params.pop("trade_date", None)
        else:
            params["trade_date"] = f"lte.{date_to}"

    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{TABLE_NAME}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"History query failed ({response.status_code}): {response.text[:300]}")
    rows = response.json()
    if not isinstance(rows, list):
        rows = []

    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        trade_date = row.get("trade_date")
        if hasattr(trade_date, "isoformat"):
            key = trade_date.isoformat()
        else:
            key = str(trade_date)
        bucket = by_date.setdefault(
            key,
            {
                "trade_date": key,
                "total": 0,
                "unsigned": 0,
                "signed": 0,
                "last_updated": None,
            },
        )
        bucket["total"] += 1
        if row.get("status") == "Signed":
            bucket["signed"] += 1
        else:
            bucket["unsigned"] += 1
        updated = row.get("updated_at")
        if hasattr(updated, "isoformat"):
            updated = updated.isoformat()
        elif updated is not None:
            updated = str(updated)
        if updated and (bucket["last_updated"] is None or updated > bucket["last_updated"]):
            bucket["last_updated"] = updated

    days = sorted(by_date.values(), key=lambda d: d["trade_date"], reverse=True)
    if unsigned_only:
        days = [d for d in days if d["unsigned"] > 0]
    return days
