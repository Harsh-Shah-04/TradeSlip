"""Client Master — parties and applicants (single-business, global)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from utils.ipo.excel_clients import parse_client_excel
from utils.ipo.models import (
    ApplicantCreate,
    ApplicantUpdate,
    PartyCreate,
    PartyUpdate,
    applicant_to_json,
    party_to_json,
)
from utils.supabase_client import _service_headers, _supabase_url

PARTIES_TABLE = "ipo_parties"
APPLICANTS_TABLE = "ipo_applicants"
POSITIONS_TABLE = "ipo_positions"
SELL_APPLICANTS_TABLE = "ipo_sell_applicants"
HTTP_TIMEOUT = 30.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Parties
# ---------------------------------------------------------------------------


def list_parties(
    *,
    include_archived: bool = False,
    status: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "order": "name.asc",
        "limit": "2000",
    }
    if not include_archived:
        params["is_archived"] = "eq.false"
    if status:
        params["status"] = f"eq.{status}"
    if search and search.strip():
        params["name"] = f"ilike.*{search.strip()}*"

    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{PARTIES_TABLE}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List parties failed ({response.status_code}): {response.text[:300]}")
    rows = response.json()
    if not isinstance(rows, list):
        rows = []

    party_ids = [str(r["id"]) for r in rows]
    counts = _applicant_counts(party_ids)
    return [party_to_json(r, applicant_count=counts.get(str(r["id"]), 0)) for r in rows]


def get_party(party_id: str) -> dict[str, Any]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{PARTIES_TABLE}",
        headers=_service_headers(),
        params={"select": "*", "id": f"eq.{party_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Fetch party failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if not (isinstance(data, list) and data):
        raise LookupError("Party not found.")
    counts = _applicant_counts([party_id])
    return party_to_json(data[0], applicant_count=counts.get(party_id, 0))


def find_party_by_name(name: str) -> dict[str, Any] | None:
    needle = name.strip()
    if not needle:
        return None
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{PARTIES_TABLE}",
        headers=_service_headers(),
        params={
            "select": "*",
            "name": f"ilike.{needle}",
            "is_archived": "eq.false",
            "limit": "5",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Find party failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if not isinstance(data, list):
        return None
    for row in data:
        if str(row.get("name") or "").strip().upper() == needle.upper():
            return party_to_json(row)
    return None


def create_party(payload: PartyCreate) -> dict[str, Any]:
    name = payload.name.strip()
    existing = find_party_by_name(name)
    if existing and not existing.get("is_archived"):
        raise ValueError(f"Party '{name}' already exists.")
    row = {
        "name": name,
        "notes": payload.notes or "",
        "status": payload.status,
        "is_archived": False,
        "updated_at": _now(),
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{PARTIES_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Create party failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    created = data[0] if isinstance(data, list) and data else data
    return get_party(str(created["id"]))


def update_party(party_id: str, payload: PartyUpdate) -> dict[str, Any]:
    get_party(party_id)
    patch: dict[str, Any] = {"updated_at": _now()}
    if payload.name is not None:
        name = payload.name.strip()
        other = find_party_by_name(name)
        if other and other["id"] != party_id:
            raise ValueError(f"Party '{name}' already exists.")
        patch["name"] = name
    if payload.notes is not None:
        patch["notes"] = payload.notes
    if payload.status is not None:
        patch["status"] = payload.status
    if payload.is_archived is not None:
        patch["is_archived"] = payload.is_archived
    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{PARTIES_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{party_id}"},
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Update party failed ({response.status_code}): {response.text[:300]}")
    return get_party(party_id)


def archive_party(party_id: str) -> dict[str, Any]:
    return update_party(party_id, PartyUpdate(is_archived=True, status="Inactive"))


def count_position_links_for_party(party_id: str) -> int:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers={**_service_headers(), "Prefer": "count=exact"},
        params={"select": "id", "party_id": f"eq.{party_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 206):
        raise RuntimeError(f"Count party positions failed ({response.status_code}): {response.text[:300]}")
    content_range = response.headers.get("content-range") or "*/0"
    try:
        return int(content_range.split("/")[-1])
    except ValueError:
        return 0


def delete_party(party_id: str) -> None:
    get_party(party_id)
    applicants = list_applicants(party_id=party_id, include_archived=True)
    for app in applicants:
        trade_count = count_links_for_applicant(app["id"])
        if trade_count > 0:
            raise PermissionError(
                f"Cannot delete party: applicant '{app['name']}' is linked to {trade_count} trade(s). "
                "Archive the party instead."
            )
    pos_count = count_position_links_for_party(party_id)
    if pos_count > 0:
        raise PermissionError(
            f"This party cannot be deleted because it is linked to {pos_count} position(s). "
            "Archive instead."
        )
    # Delete applicants first
    for app in applicants:
        _hard_delete_applicant(app["id"])
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{PARTIES_TABLE}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{party_id}"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Delete party failed ({response.status_code}): {response.text[:300]}")


def _applicant_counts(party_ids: list[str]) -> dict[str, int]:
    if not party_ids:
        return {}
    counts: dict[str, int] = {pid: 0 for pid in party_ids}
    for i in range(0, len(party_ids), 50):
        chunk = party_ids[i : i + 50]
        response = httpx.get(
            f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
            headers=_service_headers(),
            params={
                "select": "id,party_id",
                "party_id": f"in.({','.join(chunk)})",
                "is_archived": "eq.false",
                "limit": "5000",
            },
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            continue
        data = response.json()
        for row in data if isinstance(data, list) else []:
            pid = str(row.get("party_id") or "")
            counts[pid] = counts.get(pid, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Applicants
# ---------------------------------------------------------------------------


def list_applicants(
    *,
    party_id: str | None = None,
    include_archived: bool = False,
    status: str | None = None,
    search: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "order": "name.asc",
        "limit": "5000",
    }
    if party_id:
        params["party_id"] = f"eq.{party_id}"
    if not include_archived:
        params["is_archived"] = "eq.false"
    if status:
        params["status"] = f"eq.{status}"
    if category and category.strip():
        params["category"] = f"ilike.*{category.strip()}*"
    if search and search.strip():
        # PostgREST or filter
        q = search.strip()
        params["or"] = f"(name.ilike.*{q}*,pan.ilike.*{q}*,dpid.ilike.*{q}*)"

    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List applicants failed ({response.status_code}): {response.text[:300]}")
    rows = response.json()
    if not isinstance(rows, list):
        rows = []

    party_map = _party_map([str(r["party_id"]) for r in rows])
    return [
        applicant_to_json(r, party=party_map.get(str(r["party_id"])))
        for r in rows
    ]


def get_applicant(applicant_id: str) -> dict[str, Any]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
        headers=_service_headers(),
        params={"select": "*", "id": f"eq.{applicant_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Fetch applicant failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if not (isinstance(data, list) and data):
        raise LookupError("Applicant not found.")
    row = data[0]
    party = get_party(str(row["party_id"]))
    return applicant_to_json(row, party=party)


def create_applicant(payload: ApplicantCreate) -> dict[str, Any]:
    party = get_party(payload.party_id)
    if party.get("is_archived"):
        raise ValueError("Cannot add applicant to an archived party.")
    row = {
        "party_id": payload.party_id,
        "name": payload.name.strip(),
        "pan": (payload.pan or "").strip().upper(),
        "dpid": (payload.dpid or "").strip(),
        "category": (payload.category or "").strip(),
        "default_app_amount": payload.default_app_amount,
        "mobile": (payload.mobile or "").strip(),
        "email": (payload.email or "").strip(),
        "notes": payload.notes or "",
        "status": payload.status,
        "is_archived": False,
        "updated_at": _now(),
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Create applicant failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    created = data[0] if isinstance(data, list) and data else data
    return get_applicant(str(created["id"]))


def update_applicant(applicant_id: str, payload: ApplicantUpdate) -> dict[str, Any]:
    existing = get_applicant(applicant_id)
    patch: dict[str, Any] = {"updated_at": _now()}
    if payload.party_id is not None:
        get_party(payload.party_id)
        patch["party_id"] = payload.party_id
    if payload.name is not None:
        patch["name"] = payload.name.strip()
    if payload.pan is not None:
        patch["pan"] = payload.pan.strip().upper()
    if payload.dpid is not None:
        patch["dpid"] = payload.dpid.strip()
    if payload.category is not None:
        patch["category"] = payload.category.strip()
    if payload.default_app_amount is not None:
        patch["default_app_amount"] = payload.default_app_amount
    if payload.clear_default_app_amount:
        patch["default_app_amount"] = None
    if payload.mobile is not None:
        patch["mobile"] = payload.mobile.strip()
    if payload.email is not None:
        patch["email"] = payload.email.strip()
    if payload.notes is not None:
        patch["notes"] = payload.notes
    if payload.status is not None:
        patch["status"] = payload.status
    if payload.is_archived is not None:
        patch["is_archived"] = payload.is_archived

    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{applicant_id}"},
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Update applicant failed ({response.status_code}): {response.text[:300]}")
    # silence unused
    _ = existing
    return get_applicant(applicant_id)


def archive_applicant(applicant_id: str) -> dict[str, Any]:
    return update_applicant(applicant_id, ApplicantUpdate(is_archived=True, status="Inactive"))


def count_links_for_applicant(applicant_id: str) -> int:
    pos = httpx.get(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers={**_service_headers(), "Prefer": "count=exact"},
        params={"select": "id", "applicant_id": f"eq.{applicant_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    sell = httpx.get(
        f"{_supabase_url()}/rest/v1/{SELL_APPLICANTS_TABLE}",
        headers={**_service_headers(), "Prefer": "count=exact"},
        params={"select": "sell_id", "applicant_id": f"eq.{applicant_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    total = 0
    for response in (pos, sell):
        if response.status_code not in (200, 206):
            continue
        content_range = response.headers.get("content-range") or "*/0"
        try:
            total += int(content_range.split("/")[-1])
        except ValueError:
            pass
    return total


def _hard_delete_applicant(applicant_id: str) -> None:
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{applicant_id}"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Delete applicant failed ({response.status_code}): {response.text[:300]}")


def delete_applicant(applicant_id: str) -> None:
    get_applicant(applicant_id)
    links = count_links_for_applicant(applicant_id)
    if links > 0:
        raise PermissionError(
            f"This applicant cannot be deleted because they are linked to {links} trade record(s). "
            "Archive instead so historical positions and sells stay intact."
        )
    _hard_delete_applicant(applicant_id)


def _party_map(party_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not party_ids:
        return {}
    unique = list(dict.fromkeys(party_ids))
    result: dict[str, dict[str, Any]] = {}
    for i in range(0, len(unique), 50):
        chunk = unique[i : i + 50]
        response = httpx.get(
            f"{_supabase_url()}/rest/v1/{PARTIES_TABLE}",
            headers=_service_headers(),
            params={"select": "*", "id": f"in.({','.join(chunk)})"},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Fetch parties failed ({response.status_code}): {response.text[:300]}")
        data = response.json()
        for row in data if isinstance(data, list) else []:
            result[str(row["id"])] = party_to_json(row)
    return result


# ---------------------------------------------------------------------------
# Excel import
# ---------------------------------------------------------------------------


def import_clients_from_excel(file_bytes: bytes) -> dict[str, Any]:
    parsed = parse_client_excel(file_bytes)
    if not parsed:
        raise ValueError("No applicants found in the Excel file. Check the party-block format.")

    # Load existing once (bulk) — avoid per-row HTTP round-trips
    existing_parties = list_parties(include_archived=True)
    party_by_key: dict[str, dict[str, Any]] = {
        str(p.get("name") or "").strip().upper(): p for p in existing_parties
    }

    parties_created = 0
    parties_reused = 0
    needed_party_names: list[str] = []
    for row in parsed:
        key = row["party"].strip().upper()
        if not key:
            continue
        if key in party_by_key:
            continue
        if key not in {n.upper() for n in needed_party_names}:
            needed_party_names.append(row["party"].strip())

    if needed_party_names:
        now = _now()
        for name in needed_party_names:
            key = name.upper()
            if key in party_by_key:
                continue
            response = httpx.post(
                f"{_supabase_url()}/rest/v1/{PARTIES_TABLE}",
                headers=_service_headers(
                    {"Content-Type": "application/json", "Prefer": "return=representation"}
                ),
                json={
                    "name": name,
                    "notes": "",
                    "status": "Active",
                    "is_archived": False,
                    "updated_at": now,
                },
                timeout=HTTP_TIMEOUT,
            )
            if response.status_code in (200, 201):
                data = response.json()
                row = data[0] if isinstance(data, list) and data else data
                party_by_key[str(row.get("name") or "").strip().upper()] = party_to_json(row)
                parties_created += 1
                continue
            # Race / leftover from a prior partial import
            if response.status_code == 409:
                existing = find_party_by_name(name)
                if existing:
                    party_by_key[key] = existing
                    parties_reused += 1
                    continue
            raise RuntimeError(
                f"Create party '{name}' failed ({response.status_code}): {response.text[:300]}"
            )

    preexisting_keys = {
        str(p.get("name") or "").strip().upper() for p in existing_parties
    }
    parties_reused += len(
        {
            row["party"].strip().upper()
            for row in parsed
            if row["party"].strip().upper() in preexisting_keys
        }
    )

    # Index existing applicants by party for fast match
    all_applicants = list_applicants(include_archived=True)
    apps_by_party: dict[str, list[dict[str, Any]]] = {}
    for app in all_applicants:
        apps_by_party.setdefault(str(app["party_id"]), []).append(app)

    applicants_created = 0
    applicants_updated = 0
    skipped = 0
    to_insert: list[dict[str, Any]] = []
    to_update: list[tuple[str, dict[str, Any]]] = []
    now = _now()

    for row in parsed:
        party_name = row["party"].strip()
        key = party_name.upper()
        party = party_by_key.get(key)
        if not party:
            skipped += 1
            continue
        party_id = party["id"]
        name = row["name"].strip()
        pan = row["pan"].strip().upper()
        dpid = row["dpid"].strip()
        if not name:
            skipped += 1
            continue

        match = _match_applicant_in_list(
            apps_by_party.get(party_id, []), name=name, pan=pan, dpid=dpid
        )
        if match:
            patch: dict[str, Any] = {}
            if pan and pan != (match.get("pan") or "").upper():
                patch["pan"] = pan
            if dpid and dpid != (match.get("dpid") or ""):
                patch["dpid"] = dpid
            if patch:
                patch["updated_at"] = now
                to_update.append((match["id"], patch))
                # Keep in-memory index fresh
                match.update(patch)
                applicants_updated += 1
            else:
                skipped += 1
            continue

        new_app = {
            "party_id": party_id,
            "name": name,
            "pan": pan,
            "dpid": dpid,
            "category": "",
            "mobile": "",
            "email": "",
            "notes": "",
            "status": "Active",
            "is_archived": False,
            "updated_at": now,
        }
        to_insert.append(new_app)
        # Prevent duplicate inserts within same file
        apps_by_party.setdefault(party_id, []).append(
            {
                "id": f"pending-{len(to_insert)}",
                "party_id": party_id,
                "name": name,
                "pan": pan,
                "dpid": dpid,
            }
        )
        applicants_created += 1

    for i in range(0, len(to_insert), 100):
        chunk = to_insert[i : i + 100]
        response = httpx.post(
            f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
            headers=_service_headers(
                {"Content-Type": "application/json", "Prefer": "return=minimal"}
            ),
            json=chunk,
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Bulk create applicants failed ({response.status_code}): {response.text[:300]}"
            )

    for applicant_id, patch in to_update:
        response = httpx.patch(
            f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
            headers=_service_headers(
                {"Content-Type": "application/json", "Prefer": "return=minimal"}
            ),
            params={"id": f"eq.{applicant_id}"},
            json=patch,
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code not in (200, 204):
            raise RuntimeError(
                f"Update applicant failed ({response.status_code}): {response.text[:300]}"
            )

    return {
        "parties_created": parties_created,
        "parties_reused": parties_reused,
        "applicants_created": applicants_created,
        "applicants_updated": applicants_updated,
        "skipped": skipped,
        "rows_parsed": len(parsed),
    }


def _match_applicant_in_list(
    existing: list[dict[str, Any]], *, name: str, pan: str, dpid: str
) -> dict[str, Any] | None:
    name_u = name.strip().upper()
    pan_u = pan.strip().upper()
    dpid_n = dpid.strip()
    for app in existing:
        app_pan = (app.get("pan") or "").upper()
        app_name = (app.get("name") or "").upper()
        app_dpid = (app.get("dpid") or "").strip()
        if pan_u and app_pan == pan_u and app_dpid == dpid_n:
            return app
        if pan_u and app_pan == pan_u and not dpid_n:
            return app
        if not pan_u and app_name == name_u and app_dpid == dpid_n:
            return app
        if not pan_u and app_name == name_u:
            return app
    return None


def _find_existing_applicant(
    party_id: str, *, name: str, pan: str, dpid: str
) -> dict[str, Any] | None:
    existing = list_applicants(party_id=party_id, include_archived=True)
    return _match_applicant_in_list(existing, name=name, pan=pan, dpid=dpid)