from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from utils.config import DEFAULT_TEMPLATE_PATH
from utils.pdf_processor import storage_path_for

BUCKET_NAME = "trade-slips"
TABLE_NAME = "daily_trade_slips"
SIGNED_URL_EXPIRES_SECONDS = 600
DEFAULT_TEMPLATE_STORAGE_PATH = "templates/blank-trade-slip.pdf"

_client: Client | None = None
_cached_template_path: Path | None = None


def get_supabase() -> Client:
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in the environment.")

    _client = create_client(url, key)
    return _client


def upload_slip(path: str, pdf_bytes: bytes, upsert: bool) -> str:
    client = get_supabase()
    client.storage.from_(BUCKET_NAME).upload(
        path,
        pdf_bytes,
        file_options={
            "content-type": "application/pdf",
            "upsert": "true" if upsert else "false",
        },
    )
    return path


def create_signed_slip_url(storage_path: str, expires_in: int = SIGNED_URL_EXPIRES_SECONDS) -> str:
    client = get_supabase()
    result = client.storage.from_(BUCKET_NAME).create_signed_url(
        storage_path,
        expires_in,
    )
    signed_url = result.get("signedURL") or result.get("signedUrl") or ""
    if not signed_url:
        raise RuntimeError(f"Supabase did not return a signed URL for {storage_path!r}.")
    return signed_url


def upsert_slip_row(
    client_code: str,
    client_name: str,
    trade_date_iso: str,
    public_url: str,
    status: str = "Unsigned",
) -> dict[str, Any]:
    client = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "client_code": client_code,
        "client_name": client_name,
        "trade_date": trade_date_iso,
        "status": status,
        "public_url": public_url,
        "updated_at": now,
    }
    response = (
        client.table(TABLE_NAME)
        .upsert(row, on_conflict="client_code,trade_date")
        .execute()
    )
    if response.data:
        return response.data[0]
    fetch = (
        client.table(TABLE_NAME)
        .select("*")
        .eq("client_code", client_code)
        .eq("trade_date", trade_date_iso)
        .limit(1)
        .execute()
    )
    if fetch.data:
        return fetch.data[0]
    return row


def mark_signed(client_code: str, trade_date_iso: str, public_url: str) -> dict[str, Any]:
    client = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    response = (
        client.table(TABLE_NAME)
        .update(
            {
                "status": "Signed",
                "public_url": public_url,
                "updated_at": now,
            }
        )
        .eq("client_code", client_code)
        .eq("trade_date", trade_date_iso)
        .execute()
    )
    if not response.data:
        raise LookupError(
            f"No slip record for client {client_code!r} on trade date {trade_date_iso}."
        )
    return response.data[0]


def list_slips(
    trade_date_iso: str,
    status: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    client = get_supabase()
    query = client.table(TABLE_NAME).select("*").eq("trade_date", trade_date_iso)
    if status:
        query = query.eq("status", status)
    if search:
        query = query.ilike("client_code", f"%{search.strip()}%")
    response = query.order("client_code").execute()
    return response.data or []


def resolve_storage_path(client_code: str, trade_date_iso: str) -> str:
    return storage_path_for(client_code, trade_date_iso)


def download_slip_bytes(storage_path: str) -> bytes:
    client = get_supabase()
    return client.storage.from_(BUCKET_NAME).download(storage_path)


def resolve_blank_template_path(preferred: Path | None = None) -> Path:
    """
    Prefer a local template file (gitignored). If missing, download from the
    private Supabase bucket so production never needs the PDF in GitHub.
    """
    global _cached_template_path

    if preferred is not None and preferred.exists():
        return preferred

    env_local = os.environ.get("TEMPLATE_PATH", "").strip()
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

    storage_path = (
        os.environ.get("TEMPLATE_STORAGE_PATH", DEFAULT_TEMPLATE_STORAGE_PATH).strip()
        or DEFAULT_TEMPLATE_STORAGE_PATH
    )
    pdf_bytes = download_slip_bytes(storage_path)

    cache_dir = Path(tempfile.gettempdir()) / "tradeslip_templates"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / Path(storage_path).name
    cache_path.write_bytes(pdf_bytes)
    _cached_template_path = cache_path
    return cache_path
