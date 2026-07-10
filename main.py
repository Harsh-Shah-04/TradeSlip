from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from utils.config import DEFAULT_TEMPLATE_PATH, PROJECT_ROOT, TEMPLATES_DIR

load_dotenv(PROJECT_ROOT / ".env")

from utils.auth import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    clear_failed_logins,
    is_login_rate_limited,
    record_failed_login,
    resolve_authenticated_user,
    set_auth_cookies,
    sign_in_with_password,
    sign_out_supabase,
    validate_auth_config,
)
from utils.pdf_processor import GeneratedSlip, parse_trade_date_partitions, process_trades_csv
from utils.supabase_client import (
    create_signed_slip_url,
    download_slip_bytes,
    list_slips,
    mark_signed,
    resolve_blank_template_path,
    resolve_storage_path,
    upload_slip,
    upsert_slip_row,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Trade Slip Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

TEMPLATE_PATH = Path(os.environ.get("TEMPLATE_PATH", str(DEFAULT_TEMPLATE_PATH)))
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


app.add_middleware(SecurityHeadersMiddleware)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


def get_template_path() -> Path:
    preferred = TEMPLATE_PATH if TEMPLATE_PATH.is_absolute() else PROJECT_ROOT / TEMPLATE_PATH
    try:
        return resolve_blank_template_path(preferred)
    except Exception as exc:
        raise FileNotFoundError(
            "Blank trade-slip template not found locally and could not be downloaded "
            "from Supabase. Place the PDF at assets/ (local) or upload it to "
            "trade-slips/templates/blank-trade-slip.pdf (private bucket)."
        ) from exc


def validate_trade_date_iso(value: str) -> str:
    if not ISO_DATE_RE.match(value):
        raise HTTPException(status_code=400, detail="trade_date must be YYYY-MM-DD.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid trade_date.") from exc
    return value


def require_broker(
    response: Response,
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> None:
    user = resolve_authenticated_user(access_token, refresh_token, response)
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthorized")


BrokerAuth = Annotated[None, Depends(require_broker)]


def client_key_from_request(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def slip_storage_path_from_row(row: dict, trade_date_iso: str) -> str:
    client_code = row.get("client_code", "")
    stored_reference = str(row.get("public_url") or "").strip().lstrip("/")
    if stored_reference and not stored_reference.startswith("http"):
        if "/" not in stored_reference:
            return resolve_storage_path(client_code, trade_date_iso)
        return stored_reference
    return resolve_storage_path(client_code, trade_date_iso)


def slip_to_json(row: dict) -> dict:
    trade_date = row.get("trade_date")
    if hasattr(trade_date, "isoformat"):
        trade_date_iso = trade_date.isoformat()
    else:
        trade_date_iso = str(trade_date)

    updated_at = row.get("updated_at")
    if hasattr(updated_at, "isoformat"):
        updated_at = updated_at.isoformat()
    elif updated_at is not None:
        updated_at = str(updated_at)

    client_code = row.get("client_code", "")
    storage_path = slip_storage_path_from_row(row, trade_date_iso)

    return {
        "id": str(row.get("id") or ""),
        "client_code": client_code,
        "client_name": row.get("client_name"),
        "trade_date": trade_date_iso,
        "status": row.get("status"),
        "storage_path": storage_path,
        "updated_at": updated_at,
    }


def normalize_storage_path(path: str) -> str:
    cleaned = path.strip().lstrip("/")
    if not cleaned or ".." in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail="Invalid storage path.")

    parts = cleaned.split("/")
    if len(parts) != 4:
        raise HTTPException(
            status_code=400,
            detail="Storage path must be year/month/day/filename.pdf.",
        )

    year, month, day, filename = parts
    try:
        parse_trade_date_partitions(f"{year}-{month}-{day}")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid year/month/day folders in storage path.",
        ) from exc

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Storage path must reference a PDF file.")

    return cleaned


async def process_and_upload_slips(
    file_bytes: bytes,
    trade_date_iso: str,
) -> list[dict]:
    template_path = get_template_path()

    def _generate() -> list[GeneratedSlip]:
        return process_trades_csv(
            file_bytes=file_bytes,
            template_path=template_path,
            trade_date_iso=trade_date_iso,
        )

    slips = await asyncio.to_thread(_generate)
    results: list[dict] = []

    for slip in slips:
        def _upload(s: GeneratedSlip = slip) -> str:
            return upload_slip(s.storage_path, s.pdf_bytes, upsert=True)

        storage_path = await asyncio.to_thread(_upload)

        def _upsert(s: GeneratedSlip = slip, path: str = storage_path) -> dict:
            return upsert_slip_row(
                client_code=s.client_code,
                client_name=s.client_name,
                trade_date_iso=s.trade_date_iso,
                public_url=path,
                status="Unsigned",
            )

        row = await asyncio.to_thread(_upsert)
        results.append(slip_to_json(row))

    return results


def zip_entry_name(client_code: str, trade_date_iso: str, status: str) -> str:
    if status == "Signed":
        return f"{client_code}_{trade_date_iso}_SIGNED.pdf"
    return f"{client_code}_{trade_date_iso}.pdf"


def build_trade_slips_zip(trade_date_iso: str) -> bytes:
    records = list_slips(trade_date_iso=trade_date_iso)
    if not records:
        raise LookupError("No trade slips found for this date")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for row in records:
            client_code = row["client_code"]
            status = row["status"]
            storage_path = slip_storage_path_from_row(row, trade_date_iso)
            pdf_bytes = download_slip_bytes(storage_path)
            archive.writestr(
                zip_entry_name(client_code, trade_date_iso, status),
                pdf_bytes,
            )

    buffer.seek(0)
    return buffer.read()


@app.on_event("startup")
async def validate_security_config() -> None:
    validate_auth_config()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/auth/session")
async def auth_session(
    response: Response,
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> JSONResponse:
    user = resolve_authenticated_user(access_token, refresh_token, response)
    if user is not None:
        return JSONResponse(content={"authenticated": True})
    return JSONResponse(content={"authenticated": False}, status_code=401)


@app.post("/api/login")
async def login(request: Request, payload: LoginRequest) -> JSONResponse:
    client_key = client_key_from_request(request)
    if is_login_rate_limited(client_key):
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Try again in 15 minutes.",
        )

    try:
        access_token, refresh_token, expires_in = await asyncio.to_thread(
            sign_in_with_password,
            payload.email,
            payload.password,
        )
    except PermissionError as exc:
        record_failed_login(client_key)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        record_failed_login(client_key)
        logger.exception("Supabase login failed")
        raise HTTPException(status_code=401, detail="Invalid email or password.") from exc

    clear_failed_logins(client_key)
    response = JSONResponse(content={"authenticated": True})
    set_auth_cookies(response, access_token, refresh_token, expires_in)
    return response


@app.post("/api/logout")
async def logout(
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> JSONResponse:
    await asyncio.to_thread(sign_out_supabase, access_token, refresh_token)
    response = JSONResponse(content={"authenticated": False})
    clear_auth_cookies(response)
    return response


@app.get("/api/slips")
async def get_slips(
    _: BrokerAuth,
    trade_date: date | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> JSONResponse:
    resolved_date = trade_date or date.today()
    trade_date_iso = resolved_date.isoformat()

    if status is not None and status not in ("Unsigned", "Signed"):
        raise HTTPException(status_code=400, detail="status must be Unsigned, Signed, or omitted.")

    def _fetch() -> list[dict]:
        rows = list_slips(
            trade_date_iso=trade_date_iso,
            status=status,
            search=search,
        )
        return [slip_to_json(row) for row in rows]

    try:
        slips = await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.exception("Failed to list slips for %s", trade_date_iso)
        raise HTTPException(status_code=500, detail=f"Failed to load slips: {exc}") from exc

    return JSONResponse(content=slips)


@app.get("/api/slips/sign-url")
async def sign_slip_url(
    _: BrokerAuth,
    path: str = Query(..., min_length=1),
) -> JSONResponse:
    storage_path = normalize_storage_path(path)

    try:
        signed_url = await asyncio.to_thread(
            create_signed_slip_url,
            storage_path,
            600,
        )
    except Exception as exc:
        logger.exception("Failed to create signed URL for %s", storage_path)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create signed URL: {exc}",
        ) from exc

    return JSONResponse(
        content={
            "storage_path": storage_path,
            "signed_url": signed_url,
            "expires_in": 600,
        }
    )


@app.post("/api/upload-trades")
async def upload_trades(
    _: BrokerAuth,
    file: UploadFile = File(...),
    trade_date: str = Form(...),
) -> JSONResponse:
    trade_date_iso = validate_trade_date_iso(trade_date)

    filename = (file.filename or "").lower()
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a CSV.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="CSV file is empty.")

    try:
        rows = await process_and_upload_slips(file_bytes, trade_date_iso)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to process trade CSV upload")
        raise HTTPException(status_code=500, detail=f"Processing failed: {exc}") from exc

    return JSONResponse(
        content={
            "generated": len(rows),
            "slips": rows,
        }
    )


@app.post("/api/upload-signed/{client_code}/{trade_date}")
async def upload_signed(
    _: BrokerAuth,
    client_code: str,
    trade_date: str,
    file: UploadFile = File(...),
) -> JSONResponse:
    trade_date_iso = validate_trade_date_iso(trade_date)

    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    if "pdf" not in content_type and not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Signed slip must be a PDF file.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="PDF file is empty.")

    storage_path = resolve_storage_path(client_code, trade_date_iso)

    try:
        def _upload() -> str:
            return upload_slip(storage_path, pdf_bytes, upsert=True)

        uploaded_path = await asyncio.to_thread(_upload)

        def _mark() -> dict:
            return mark_signed(client_code, trade_date_iso, uploaded_path)

        row = await asyncio.to_thread(_mark)
        payload = slip_to_json(row)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to upload signed slip for %s", client_code)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    return JSONResponse(content=payload)


@app.get("/api/download-zip/{trade_date}")
async def download_zip(
    _: BrokerAuth,
    trade_date: str,
) -> StreamingResponse:
    trade_date_iso = validate_trade_date_iso(trade_date)

    try:
        zip_bytes = await asyncio.to_thread(build_trade_slips_zip, trade_date_iso)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="No trade slips found for this date.") from exc
    except Exception as exc:
        logger.exception("Failed to build trade slips ZIP for %s", trade_date_iso)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to build ZIP archive: {exc}",
        ) from exc

    zip_stream = io.BytesIO(zip_bytes)
    zip_stream.seek(0)
    filename = f"TradeSlips_{trade_date_iso}.zip"
    return StreamingResponse(
        zip_stream,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Run locally: uvicorn main:app --reload
