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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from utils.config import DEFAULT_TEMPLATE_PATH, PROJECT_ROOT, STATIC_DIR, TEMPLATES_DIR

load_dotenv(PROJECT_ROOT / ".env")

from utils.auth import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    BrokerSession,
    clear_auth_cookies,
    clear_failed_logins,
    is_login_rate_limited,
    record_failed_login,
    resolve_authenticated_broker,
    set_auth_cookies,
    sign_in_with_password,
    sign_out_supabase,
    validate_auth_config,
)
from utils.pdf_processor import GeneratedSlip, parse_trade_date_partitions, process_trades_csv
from utils.ipo.models import (
    AllocationSet,
    ApplicantCreate,
    ApplicantUpdate,
    IpoMasterCreate,
    IpoMasterUpdate,
    PartyCreate,
    PartyUpdate,
    PositionCreate,
    PositionUpdate,
    SellCreate,
    SellUpdate,
)
from utils.ipo.clients import (
    archive_applicant,
    archive_party,
    create_applicant,
    create_party,
    delete_applicant,
    delete_party,
    import_clients_from_excel,
    list_applicants,
    list_parties,
    update_applicant,
    update_party,
)
from utils.ipo.service import (
    archive_ipo,
    clear_position_allocations,
    count_positions_for_ipo,
    create_ipo,
    create_position,
    create_sell,
    delete_ipo,
    delete_position,
    delete_sell,
    get_ipo,
    get_position,
    list_category_labels as list_ipo_category_labels,
    list_ipos,
    list_positions,
    set_position_allocations,
    update_ipo,
    update_position,
    update_sell,
)
from utils.supabase_client import (
    broker_owns_storage_path,
    change_user_password,
    create_auth_user,
    create_signed_slip_url,
    delete_slip_row,
    delete_storage_object,
    download_slip_bytes_many,
    get_broker_by_email,
    get_slip_row,
    list_brokers,
    list_history_days,
    list_slips,
    mark_signed,
    resolve_blank_template_path,
    resolve_storage_path,
    set_broker_active,
    update_broker_profile,
    update_slip_row,
    upload_slip,
    upsert_broker_row,
    upsert_slip_row,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Trade Slip Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

TEMPLATE_PATH = Path(os.environ.get("TEMPLATE_PATH", str(DEFAULT_TEMPLATE_PATH)))
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


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


class SlipRef(BaseModel):
    client_code: str = Field(min_length=1, max_length=64)
    trade_date: str = Field(min_length=10, max_length=10)


class BulkDeleteRequest(BaseModel):
    items: list[SlipRef] = Field(min_length=1, max_length=500)


class BulkZipRequest(BaseModel):
    # Keep bulk small enough for Vercel serverless timeouts.
    dates: list[str] = Field(min_length=1, max_length=5)

    @field_validator("dates")
    @classmethod
    def validate_dates(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not ISO_DATE_RE.match(value):
                raise ValueError("Each date must be YYYY-MM-DD.")
            datetime.strptime(value, "%Y-%m-%d")
            if value not in seen:
                cleaned.append(value)
                seen.add(value)
        return cleaned


BULK_ZIP_MAX_FILES = 100
ZIP_DOWNLOAD_WORKERS = 12


class InviteBrokerRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="broker")

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if value not in ("admin", "broker"):
            raise ValueError("role must be admin or broker.")
        return value


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


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
) -> BrokerSession:
    broker = resolve_authenticated_broker(access_token, refresh_token, response)
    if broker is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return broker


def require_admin(broker: Annotated[BrokerSession, Depends(require_broker)]) -> BrokerSession:
    if not broker.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return broker


BrokerAuth = Annotated[BrokerSession, Depends(require_broker)]
AdminAuth = Annotated[BrokerSession, Depends(require_admin)]


def optional_broker(
    response: Response,
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> BrokerSession | None:
    return resolve_authenticated_broker(access_token, refresh_token, response)


def client_key_from_request(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def slip_storage_path_from_row(row: dict, trade_date_iso: str, broker_id: str) -> str:
    client_code = row.get("client_code", "")
    stored_reference = str(row.get("public_url") or "").strip().lstrip("/")
    if stored_reference and not stored_reference.startswith("http"):
        if "/" not in stored_reference:
            return resolve_storage_path(client_code, trade_date_iso, broker_id)
        return stored_reference
    return resolve_storage_path(client_code, trade_date_iso, broker_id)


def slip_to_json(row: dict, broker_id: str) -> dict:
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
    storage_path = slip_storage_path_from_row(row, trade_date_iso, broker_id)

    return {
        "id": str(row.get("id") or ""),
        "broker_id": str(row.get("broker_id") or broker_id),
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
    # Legacy: year/month/day/file.pdf
    # Multi-broker: broker_uuid/year/month/day/file.pdf
    if len(parts) == 4:
        year, month, day, filename = parts
    elif len(parts) == 5 and UUID_RE.match(parts[0]):
        _, year, month, day, filename = parts
    else:
        raise HTTPException(
            status_code=400,
            detail="Storage path must be year/month/day/file.pdf or broker_id/year/month/day/file.pdf.",
        )

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


def page_context(request: Request, broker: BrokerSession | None, **extra: object) -> dict:
    ctx = {
        "request": request,
        "broker": broker,
        "nav_active": extra.pop("nav_active", ""),
        "is_admin": bool(broker and broker.is_admin),
        "module": extra.pop("module", "tradeslip"),
    }
    ctx.update(extra)
    return ctx


async def process_and_upload_slips(
    file_bytes: bytes,
    trade_date_iso: str,
    broker_id: str,
) -> list[dict]:
    template_path = get_template_path()

    def _generate() -> list[GeneratedSlip]:
        return process_trades_csv(
            file_bytes=file_bytes,
            template_path=template_path,
            trade_date_iso=trade_date_iso,
            broker_id=broker_id,
        )

    slips = await asyncio.to_thread(_generate)
    results: list[dict] = []

    for slip in slips:
        def _upload(s: GeneratedSlip = slip) -> str:
            return upload_slip(s.storage_path, s.pdf_bytes, upsert=True)

        storage_path = await asyncio.to_thread(_upload)

        def _upsert(s: GeneratedSlip = slip, path: str = storage_path) -> dict:
            return upsert_slip_row(
                broker_id=broker_id,
                client_code=s.client_code,
                client_name=s.client_name,
                trade_date_iso=s.trade_date_iso,
                public_url=path,
                status="Unsigned",
            )

        row = await asyncio.to_thread(_upsert)
        results.append(slip_to_json(row, broker_id))

    return results


def zip_entry_name(client_code: str, trade_date_iso: str, status: str) -> str:
    if status == "Signed":
        return f"{client_code}_{trade_date_iso}_SIGNED.pdf"
    return f"{client_code}_{trade_date_iso}.pdf"


def _write_zip_entries(entries: list[tuple[str, bytes]]) -> bytes:
    """Build a ZIP. PDFs are already compressed, so store without re-deflating."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name, pdf_bytes in entries:
            archive.writestr(name, pdf_bytes)
    buffer.seek(0)
    return buffer.read()


def build_trade_slips_zip(broker_id: str, trade_date_iso: str, folder_prefix: str = "") -> bytes:
    records = list_slips(broker_id=broker_id, trade_date_iso=trade_date_iso)
    if not records:
        raise LookupError("No trade slips found for this date")

    jobs: list[tuple[str, str]] = []  # (zip_name, storage_path)
    for row in records:
        client_code = row["client_code"]
        status = row["status"]
        storage_path = slip_storage_path_from_row(row, trade_date_iso, broker_id)
        name = zip_entry_name(client_code, trade_date_iso, status)
        if folder_prefix:
            name = f"{folder_prefix.rstrip('/')}/{name}"
        jobs.append((name, storage_path))

    downloaded = download_slip_bytes_many(
        [path for _, path in jobs],
        max_workers=ZIP_DOWNLOAD_WORKERS,
    )
    entries: list[tuple[str, bytes]] = []
    for name, path in jobs:
        pdf_bytes = downloaded.get(path)
        if pdf_bytes is None:
            continue
        entries.append((name, pdf_bytes))
    if not entries:
        raise RuntimeError("Could not download any PDFs for this date.")
    return _write_zip_entries(entries)


def build_multi_day_zip(broker_id: str, dates: list[str]) -> bytes:
    jobs: list[tuple[str, str]] = []  # (zip_name, storage_path)
    for trade_date_iso in dates:
        records = list_slips(broker_id=broker_id, trade_date_iso=trade_date_iso)
        for row in records:
            client_code = row["client_code"]
            status = row["status"]
            storage_path = slip_storage_path_from_row(row, trade_date_iso, broker_id)
            jobs.append(
                (
                    f"{trade_date_iso}/{zip_entry_name(client_code, trade_date_iso, status)}",
                    storage_path,
                )
            )

    if not jobs:
        raise LookupError("No trade slips found for the selected dates")
    if len(jobs) > BULK_ZIP_MAX_FILES:
        raise ValueError(
            f"Selected days include {len(jobs)} slips (max {BULK_ZIP_MAX_FILES}). "
            "Select fewer days, or download one day at a time."
        )

    downloaded = download_slip_bytes_many(
        [path for _, path in jobs],
        max_workers=ZIP_DOWNLOAD_WORKERS,
    )
    entries: list[tuple[str, bytes]] = []
    for name, path in jobs:
        pdf_bytes = downloaded.get(path)
        if pdf_bytes is None:
            continue
        entries.append((name, pdf_bytes))
    if not entries:
        raise RuntimeError("Could not download any PDFs for the selected dates.")
    return _write_zip_entries(entries)


def broker_to_json(row: dict) -> dict:
    created = row.get("created_at")
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    elif created is not None:
        created = str(created)
    return {
        "id": str(row.get("id") or ""),
        "email": row.get("email"),
        "display_name": row.get("display_name"),
        "role": row.get("role"),
        "is_active": bool(row.get("is_active", True)),
        "created_at": created,
    }


@app.on_event("startup")
async def validate_security_config() -> None:
    validate_auth_config()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def root(
    request: Request,
    response: Response,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is None:
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is not None:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", page_context(request, None, nav_active="login"))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard.html",
        page_context(request, broker, nav_active="dashboard"),
    )


@app.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "history.html",
        page_context(request, broker, nav_active="history"),
    )


@app.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "account.html",
        page_context(request, broker, nav_active="account"),
    )


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is None:
        return RedirectResponse(url="/login", status_code=302)
    if not broker.is_admin:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        "admin_users.html",
        page_context(request, broker, nav_active="admin"),
    )


@app.get("/ipo", response_class=HTMLResponse)
async def ipo_page(
    request: Request,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "ipo.html",
        page_context(request, broker, nav_active="ipo_positions", module="ipo"),
    )


@app.get("/ipo/master", response_class=HTMLResponse)
async def ipo_master_page(
    request: Request,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "ipo_master.html",
        page_context(request, broker, nav_active="ipo_master", module="ipo"),
    )


@app.get("/ipo/clients", response_class=HTMLResponse)
async def ipo_clients_page(
    request: Request,
    broker: Annotated[BrokerSession | None, Depends(optional_broker)] = None,
):
    if broker is None:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "ipo_clients.html",
        page_context(request, broker, nav_active="ipo_clients", module="ipo"),
    )


# ---------------------------------------------------------------------------
# Auth API
# ---------------------------------------------------------------------------


@app.get("/api/auth/session")
async def auth_session(
    response: Response,
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> JSONResponse:
    broker = resolve_authenticated_broker(access_token, refresh_token, response)
    if broker is not None:
        return JSONResponse(content=broker.to_dict())
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
        access_token, refresh_token, expires_in, broker = await asyncio.to_thread(
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
    response = JSONResponse(content=broker.to_dict())
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


@app.patch("/api/account/profile")
async def update_profile(broker: BrokerAuth, payload: ProfileUpdateRequest) -> JSONResponse:
    try:
        row = await asyncio.to_thread(update_broker_profile, broker.id, payload.display_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=broker_to_json(row))


@app.post("/api/account/password")
async def change_password(
    broker: BrokerAuth,
    payload: PasswordChangeRequest,
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE_NAME),
) -> JSONResponse:
    if not access_token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        await asyncio.to_thread(change_user_password, access_token, payload.new_password)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Password change failed for %s", broker.email)
        raise HTTPException(status_code=500, detail="Could not change password.") from exc
    return JSONResponse(content={"ok": True})


# ---------------------------------------------------------------------------
# Slips API
# ---------------------------------------------------------------------------


@app.get("/api/slips")
async def get_slips(
    broker: BrokerAuth,
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
            broker_id=broker.id,
            trade_date_iso=trade_date_iso,
            status=status,
            search=search,
        )
        return [slip_to_json(row, broker.id) for row in rows]

    try:
        slips = await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.exception("Failed to list slips for %s", trade_date_iso)
        raise HTTPException(status_code=500, detail=f"Failed to load slips: {exc}") from exc

    return JSONResponse(content=slips)


@app.get("/api/slips/sign-url")
async def sign_slip_url(
    broker: BrokerAuth,
    path: str = Query(..., min_length=1),
) -> JSONResponse:
    storage_path = normalize_storage_path(path)
    owned = await asyncio.to_thread(broker_owns_storage_path, broker.id, storage_path)
    if not owned:
        raise HTTPException(status_code=404, detail="Slip not found.")

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
    broker: BrokerAuth,
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
        rows = await process_and_upload_slips(file_bytes, trade_date_iso, broker.id)
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
    broker: BrokerAuth,
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

    try:
        existing = await asyncio.to_thread(get_slip_row, broker.id, client_code, trade_date_iso)
        storage_path = slip_storage_path_from_row(existing, trade_date_iso, broker.id)

        def _upload() -> str:
            return upload_slip(storage_path, pdf_bytes, upsert=True)

        uploaded_path = await asyncio.to_thread(_upload)

        def _mark() -> dict:
            return mark_signed(broker.id, client_code, trade_date_iso, uploaded_path)

        row = await asyncio.to_thread(_mark)
        payload = slip_to_json(row, broker.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to upload signed slip for %s", client_code)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    return JSONResponse(content=payload)


async def _read_optional_pdf(file: UploadFile | None) -> bytes | None:
    if file is None:
        return None
    filename = (file.filename or "").strip()
    if not filename:
        return None
    content_type = (file.content_type or "").lower()
    if "pdf" not in content_type and not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Replacement file must be a PDF.")
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="PDF file is empty.")
    return pdf_bytes


@app.patch("/api/slips/{client_code}/{trade_date}")
async def patch_slip(
    broker: BrokerAuth,
    client_code: str,
    trade_date: str,
    client_name: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> JSONResponse:
    trade_date_iso = validate_trade_date_iso(trade_date)
    name_value = (client_name or "").strip() or None
    pdf_bytes = await _read_optional_pdf(file)

    if name_value is None and pdf_bytes is None:
        raise HTTPException(
            status_code=400,
            detail="Provide a client_name and/or a PDF file to update.",
        )

    try:
        existing = await asyncio.to_thread(get_slip_row, broker.id, client_code, trade_date_iso)
        storage_path = slip_storage_path_from_row(existing, trade_date_iso, broker.id)

        if pdf_bytes is not None:
            await asyncio.to_thread(upload_slip, storage_path, pdf_bytes, True)

        row = await asyncio.to_thread(
            update_slip_row,
            broker.id,
            client_code,
            trade_date_iso,
            client_name=name_value,
            public_url=storage_path if pdf_bytes is not None else None,
            status=None,
        )
        return JSONResponse(content=slip_to_json(row, broker.id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to patch slip %s %s", client_code, trade_date_iso)
        raise HTTPException(status_code=500, detail=f"Update failed: {exc}") from exc


@app.post("/api/slips/{client_code}/{trade_date}/reupload-unsigned")
async def reupload_unsigned(
    broker: BrokerAuth,
    client_code: str,
    trade_date: str,
    file: UploadFile = File(...),
) -> JSONResponse:
    trade_date_iso = validate_trade_date_iso(trade_date)
    pdf_bytes = await _read_optional_pdf(file)
    if pdf_bytes is None:
        raise HTTPException(status_code=400, detail="Unsigned slip PDF is required.")

    try:
        existing = await asyncio.to_thread(get_slip_row, broker.id, client_code, trade_date_iso)
        storage_path = slip_storage_path_from_row(existing, trade_date_iso, broker.id)
        await asyncio.to_thread(upload_slip, storage_path, pdf_bytes, True)
        row = await asyncio.to_thread(
            update_slip_row,
            broker.id,
            client_code,
            trade_date_iso,
            public_url=storage_path,
            status="Unsigned",
        )
        return JSONResponse(content=slip_to_json(row, broker.id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to reupload unsigned slip %s", client_code)
        raise HTTPException(status_code=500, detail=f"Reupload failed: {exc}") from exc


def _delete_slip_pair(broker_id: str, client_code: str, trade_date_iso: str) -> None:
    existing = get_slip_row(broker_id, client_code, trade_date_iso)
    storage_path = slip_storage_path_from_row(existing, trade_date_iso, broker_id)
    try:
        delete_storage_object(storage_path)
    except Exception:
        logger.exception("Storage delete failed for %s (continuing with DB delete)", storage_path)
    delete_slip_row(broker_id, client_code, trade_date_iso)


@app.delete("/api/slips/{client_code}/{trade_date}")
async def delete_slip(
    broker: BrokerAuth,
    client_code: str,
    trade_date: str,
) -> JSONResponse:
    trade_date_iso = validate_trade_date_iso(trade_date)
    try:
        await asyncio.to_thread(_delete_slip_pair, broker.id, client_code, trade_date_iso)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete slip %s %s", client_code, trade_date_iso)
        raise HTTPException(status_code=500, detail=f"Delete failed: {exc}") from exc

    return JSONResponse(
        content={
            "deleted": True,
            "client_code": client_code,
            "trade_date": trade_date_iso,
        }
    )


@app.post("/api/slips/bulk-delete")
async def bulk_delete_slips(
    broker: BrokerAuth,
    payload: BulkDeleteRequest,
) -> JSONResponse:
    deleted: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for item in payload.items:
        try:
            trade_date_iso = validate_trade_date_iso(item.trade_date)
            await asyncio.to_thread(_delete_slip_pair, broker.id, item.client_code, trade_date_iso)
            deleted.append(
                {"client_code": item.client_code, "trade_date": trade_date_iso}
            )
        except HTTPException as exc:
            failed.append(
                {
                    "client_code": item.client_code,
                    "trade_date": item.trade_date,
                    "error": str(exc.detail),
                }
            )
        except Exception as exc:
            logger.exception(
                "Bulk delete failed for %s %s", item.client_code, item.trade_date
            )
            failed.append(
                {
                    "client_code": item.client_code,
                    "trade_date": item.trade_date,
                    "error": str(exc),
                }
            )

    return JSONResponse(
        content={
            "deleted": deleted,
            "failed": failed,
            "deleted_count": len(deleted),
            "failed_count": len(failed),
        }
    )


@app.get("/api/history/days")
async def history_days(
    broker: BrokerAuth,
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    unsigned_only: bool = Query(default=False),
) -> JSONResponse:
    if date_from:
        validate_trade_date_iso(date_from)
    if date_to:
        validate_trade_date_iso(date_to)
    try:
        days = await asyncio.to_thread(
            list_history_days,
            broker.id,
            date_from,
            date_to,
            unsigned_only,
        )
    except Exception as exc:
        logger.exception("Failed to load history days")
        raise HTTPException(status_code=500, detail=f"Failed to load history: {exc}") from exc
    return JSONResponse(content={"days": days})


@app.get("/api/download-zip/{trade_date}")
async def download_zip(
    broker: BrokerAuth,
    trade_date: str,
) -> StreamingResponse:
    trade_date_iso = validate_trade_date_iso(trade_date)

    try:
        zip_bytes = await asyncio.to_thread(build_trade_slips_zip, broker.id, trade_date_iso)
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


@app.post("/api/download-zip/bulk")
async def download_zip_bulk(
    broker: BrokerAuth,
    payload: BulkZipRequest,
) -> StreamingResponse:
    try:
        zip_bytes = await asyncio.to_thread(build_multi_day_zip, broker.id, payload.dates)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to build multi-day ZIP")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to build ZIP archive: {exc}",
        ) from exc

    zip_stream = io.BytesIO(zip_bytes)
    zip_stream.seek(0)
    filename = f"TradeSlips_bulk_{len(payload.dates)}_days.zip"
    return StreamingResponse(
        zip_stream,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------


@app.get("/api/admin/brokers")
async def admin_list_brokers(_: AdminAuth) -> JSONResponse:
    try:
        rows = await asyncio.to_thread(list_brokers)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"brokers": [broker_to_json(row) for row in rows]})


@app.post("/api/admin/brokers")
async def admin_invite_broker(_: AdminAuth, payload: InviteBrokerRequest) -> JSONResponse:
    email = payload.email.strip().lower()
    display_name = (payload.display_name or "").strip() or email.split("@")[0]

    existing = await asyncio.to_thread(get_broker_by_email, email)
    if existing is not None:
        raise HTTPException(status_code=400, detail="A broker with this email already exists.")

    try:
        auth_user = await asyncio.to_thread(
            create_auth_user,
            email,
            payload.password,
            display_name,
        )
        broker_id = str(auth_user["id"])
        row = await asyncio.to_thread(
            upsert_broker_row,
            broker_id,
            email,
            display_name,
            payload.role,
            True,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to invite broker %s", email)
        raise HTTPException(status_code=500, detail=f"Invite failed: {exc}") from exc

    return JSONResponse(content=broker_to_json(row), status_code=201)


@app.patch("/api/admin/brokers/{broker_id}/deactivate")
async def admin_deactivate_broker(
    admin: AdminAuth,
    broker_id: str,
    activate: bool = Query(default=False),
) -> JSONResponse:
    if broker_id == admin.id and not activate:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    try:
        row = await asyncio.to_thread(set_broker_active, broker_id, activate)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=broker_to_json(row))


# ---------------------------------------------------------------------------
# IPO Trading API
# ---------------------------------------------------------------------------


@app.get("/api/ipo/category-labels")
async def ipo_category_labels(_: BrokerAuth) -> JSONResponse:
    try:
        labels = await asyncio.to_thread(list_ipo_category_labels)
    except Exception as exc:
        logger.exception("Failed to list IPO category labels")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"labels": labels})


# ---------------------------------------------------------------------------
# Client Master API
# ---------------------------------------------------------------------------


@app.get("/api/ipo/parties")
async def ipo_list_parties(
    _: BrokerAuth,
    include_archived: bool = Query(default=False),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> JSONResponse:
    try:
        items = await asyncio.to_thread(
            list_parties,
            include_archived=include_archived,
            status=status,
            search=search,
        )
    except Exception as exc:
        logger.exception("Failed to list parties")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"parties": items})


@app.post("/api/ipo/parties")
async def ipo_create_party(admin: AdminAuth, payload: PartyCreate) -> JSONResponse:
    try:
        item = await asyncio.to_thread(create_party, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create party")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item, status_code=201)


@app.patch("/api/ipo/parties/{party_id}")
async def ipo_patch_party(admin: AdminAuth, party_id: str, payload: PartyUpdate) -> JSONResponse:
    try:
        item = await asyncio.to_thread(update_party, party_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item)


@app.post("/api/ipo/parties/{party_id}/archive")
async def ipo_archive_party(admin: AdminAuth, party_id: str) -> JSONResponse:
    try:
        item = await asyncio.to_thread(archive_party, party_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item)


@app.delete("/api/ipo/parties/{party_id}")
async def ipo_delete_party(admin: AdminAuth, party_id: str) -> JSONResponse:
    try:
        await asyncio.to_thread(delete_party, party_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"deleted": True, "id": party_id})


@app.get("/api/ipo/applicants")
async def ipo_list_applicants(
    _: BrokerAuth,
    party_id: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> JSONResponse:
    try:
        items = await asyncio.to_thread(
            list_applicants,
            party_id=party_id,
            include_archived=include_archived,
            status=status,
            search=search,
            category=category,
        )
    except Exception as exc:
        logger.exception("Failed to list applicants")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"applicants": items})


@app.post("/api/ipo/applicants")
async def ipo_create_applicant(admin: AdminAuth, payload: ApplicantCreate) -> JSONResponse:
    try:
        item = await asyncio.to_thread(create_applicant, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create applicant")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item, status_code=201)


@app.patch("/api/ipo/applicants/{applicant_id}")
async def ipo_patch_applicant(
    admin: AdminAuth, applicant_id: str, payload: ApplicantUpdate
) -> JSONResponse:
    try:
        item = await asyncio.to_thread(update_applicant, applicant_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item)


@app.post("/api/ipo/applicants/{applicant_id}/archive")
async def ipo_archive_applicant(admin: AdminAuth, applicant_id: str) -> JSONResponse:
    try:
        item = await asyncio.to_thread(archive_applicant, applicant_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item)


@app.delete("/api/ipo/applicants/{applicant_id}")
async def ipo_delete_applicant(admin: AdminAuth, applicant_id: str) -> JSONResponse:
    try:
        await asyncio.to_thread(delete_applicant, applicant_id)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"deleted": True, "id": applicant_id})


@app.post("/api/ipo/clients/import")
async def ipo_import_clients(
    admin: AdminAuth,
    file: UploadFile = File(...),
) -> JSONResponse:
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Upload an Excel .xlsx file.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    try:
        result = await asyncio.to_thread(import_clients_from_excel, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Client Excel import failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=result)


@app.get("/api/ipo/master")
async def ipo_list_master(
    _: BrokerAuth,
    status: str | None = Query(default=None),
    active_only: bool = Query(default=False),
    include_archived: bool = Query(default=False),
) -> JSONResponse:
    try:
        items = await asyncio.to_thread(
            list_ipos,
            status=status,
            include_archived=include_archived,
            active_only=active_only,
        )
    except Exception as exc:
        logger.exception("Failed to list IPO master")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"ipos": items})


@app.post("/api/ipo/master")
async def ipo_create_master(admin: AdminAuth, payload: IpoMasterCreate) -> JSONResponse:
    try:
        item = await asyncio.to_thread(create_ipo, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create IPO")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item, status_code=201)


@app.patch("/api/ipo/master/{ipo_id}")
async def ipo_patch_master(
    admin: AdminAuth,
    ipo_id: str,
    payload: IpoMasterUpdate,
) -> JSONResponse:
    try:
        item = await asyncio.to_thread(update_ipo, ipo_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to update IPO %s", ipo_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item)


@app.post("/api/ipo/master/{ipo_id}/archive")
async def ipo_archive_master(admin: AdminAuth, ipo_id: str) -> JSONResponse:
    try:
        item = await asyncio.to_thread(archive_ipo, ipo_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item)


@app.delete("/api/ipo/master/{ipo_id}")
async def ipo_delete_master(admin: AdminAuth, ipo_id: str) -> JSONResponse:
    try:
        await asyncio.to_thread(delete_ipo, ipo_id)
    except PermissionError as exc:
        count = await asyncio.to_thread(count_positions_for_ipo, ipo_id)
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "trade_count": count},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"deleted": True, "id": ipo_id})


@app.get("/api/ipo/positions")
async def ipo_get_positions(
    broker: BrokerAuth,
    ipo_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    party: str | None = Query(default=None),
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
) -> JSONResponse:
    if date_from:
        validate_trade_date_iso(date_from)
    if date_to:
        validate_trade_date_iso(date_to)
    if status and status not in ("Open", "Partially Sold", "Closed"):
        raise HTTPException(status_code=400, detail="Invalid status filter.")
    try:
        items = await asyncio.to_thread(
            list_positions,
            broker.id,
            ipo_id=ipo_id,
            status=status,
            party=party,
            date_from=date_from,
            date_to=date_to,
        )
    except Exception as exc:
        logger.exception("Failed to list positions")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"positions": items})


@app.post("/api/ipo/positions")
async def ipo_create_position(broker: BrokerAuth, payload: PositionCreate) -> JSONResponse:
    try:
        validate_trade_date_iso(payload.trade_date)
        if payload.include_sell and payload.sell_date:
            validate_trade_date_iso(payload.sell_date)
        item = await asyncio.to_thread(create_position, broker.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create position")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item, status_code=201)


@app.get("/api/ipo/positions/{position_id}")
async def ipo_get_position(broker: BrokerAuth, position_id: str) -> JSONResponse:
    try:
        item = await asyncio.to_thread(get_position, broker.id, position_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item)


@app.patch("/api/ipo/positions/{position_id}")
async def ipo_patch_position(
    broker: BrokerAuth,
    position_id: str,
    payload: PositionUpdate,
) -> JSONResponse:
    try:
        if payload.trade_date:
            validate_trade_date_iso(payload.trade_date)
        item = await asyncio.to_thread(update_position, broker.id, position_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=item)


@app.delete("/api/ipo/positions/{position_id}")
async def ipo_delete_position(broker: BrokerAuth, position_id: str) -> JSONResponse:
    try:
        await asyncio.to_thread(delete_position, broker.id, position_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"deleted": True, "id": position_id})


@app.post("/api/ipo/positions/{position_id}/sells")
async def ipo_create_sell(
    broker: BrokerAuth,
    position_id: str,
    payload: SellCreate,
) -> JSONResponse:
    try:
        validate_trade_date_iso(payload.sell_date)
        result = await asyncio.to_thread(create_sell, broker.id, position_id, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create sell")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=result, status_code=201)


@app.patch("/api/ipo/positions/{position_id}/sells/{sell_id}")
async def ipo_patch_sell(
    broker: BrokerAuth,
    position_id: str,
    sell_id: str,
    payload: SellUpdate,
) -> JSONResponse:
    try:
        if payload.sell_date:
            validate_trade_date_iso(payload.sell_date)
        result = await asyncio.to_thread(
            update_sell, broker.id, position_id, sell_id, payload
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=result)


@app.delete("/api/ipo/positions/{position_id}/sells/{sell_id}")
async def ipo_delete_sell(
    broker: BrokerAuth,
    position_id: str,
    sell_id: str,
) -> JSONResponse:
    try:
        position = await asyncio.to_thread(delete_sell, broker.id, position_id, sell_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content={"deleted": True, "position": position})


@app.put("/api/ipo/positions/{position_id}/allocations")
async def ipo_set_allocations(
    broker: BrokerAuth,
    position_id: str,
    payload: AllocationSet,
) -> JSONResponse:
    try:
        position = await asyncio.to_thread(
            set_position_allocations, broker.id, position_id, payload
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to set allocations")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=position)


@app.delete("/api/ipo/positions/{position_id}/allocations")
async def ipo_clear_allocations(broker: BrokerAuth, position_id: str) -> JSONResponse:
    try:
        position = await asyncio.to_thread(clear_position_allocations, broker.id, position_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(content=position)


# Run locally: uvicorn main:app --reload
