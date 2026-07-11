#!/usr/bin/env python3
"""Print a ready-to-run multi-broker migration SQL for the bootstrap admin.

Usage (from project root, with .env loaded):
  python scripts/prepare_migration.py

Then paste the printed SQL into the Supabase SQL editor.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT))

EMAIL = (
    os.environ.get("ADMIN_BOOTSTRAP_EMAIL")
    or os.environ.get("ALLOWED_EMAIL")
    or ""
).strip().lower().split(",")[0].strip()
URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
KEY = os.environ.get("SUPABASE_KEY") or ""


def main() -> None:
    if not EMAIL or not URL or not KEY:
        raise SystemExit("Need SUPABASE_URL, SUPABASE_KEY, and ADMIN_BOOTSTRAP_EMAIL or ALLOWED_EMAIL")

    response = httpx.get(
        f"{URL}/auth/v1/admin/users",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"},
        params={"page": 1, "per_page": 200},
        timeout=30,
    )
    if response.status_code != 200:
        raise SystemExit(f"Auth admin list failed: {response.status_code} {response.text[:300]}")

    payload = response.json()
    users = payload.get("users") if isinstance(payload, dict) else payload
    if not isinstance(users, list):
        raise SystemExit(f"Unexpected users payload: {payload!r}")

    match = next((u for u in users if str(u.get("email") or "").lower() == EMAIL), None)
    if not match:
        raise SystemExit(f"No auth user found for {EMAIL!r}. Create the user in Supabase Auth first.")

    user_id = match["id"]
    sql_path = ROOT / "supabase" / "migration_multi_broker.sql"
    sql = sql_path.read_text(encoding="utf-8")
    sql = sql.replace("YOUR_FATHER_USER_UUID", user_id)
    sql = sql.replace("YOUR_FATHER_EMAIL", EMAIL)
    print(sql)
    print("\n-- Prepared for", EMAIL, user_id, file=sys.stderr)


if __name__ == "__main__":
    main()
