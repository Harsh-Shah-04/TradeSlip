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
    client_code: str,
    client_name: str,
    trade_date_iso: str,
    public_url: str,
    status: str = "Unsigned",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    row = {
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
        params={"on_conflict": "client_code,trade_date"},
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


def mark_signed(client_code: str, trade_date_iso: str, public_url: str) -> dict[str, Any]:
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
    trade_date_iso: str,
    status: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
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


def resolve_storage_path(client_code: str, trade_date_iso: str) -> str:
    return storage_path_for(client_code, trade_date_iso)


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
    """
    Prefer a local template file (gitignored). If missing, download from the
    private Supabase bucket so production never needs the PDF in GitHub.
    """
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
