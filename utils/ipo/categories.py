from __future__ import annotations

# Excel shorthand labels → future category group.
# Phase 1 stores the shorthand on each trade; group is snapshotted when known.

CATEGORY_SEEDS: list[dict[str, object]] = [
    {"code": "15K", "category_group": "Retail", "display_order": 10},
    {"code": "2-SHARE", "category_group": "Retail", "display_order": 20},
    {"code": "2- SHARE", "category_group": "Retail", "display_order": 21},
    {"code": "2+", "category_group": "Small HNI", "display_order": 30},
    {"code": "10+", "category_group": "Big HNI", "display_order": 40},
    {"code": "SHARE", "category_group": "Shareholder", "display_order": 50},
    {"code": "15K (Shareholder)", "category_group": "Shareholder", "display_order": 60},
    {"code": "Retail", "category_group": "Retail", "display_order": 5},
    {"code": "Small HNI", "category_group": "Small HNI", "display_order": 25},
    {"code": "sHNI", "category_group": "Small HNI", "display_order": 26},
    {"code": "Big HNI", "category_group": "Big HNI", "display_order": 35},
    {"code": "bHNI", "category_group": "Big HNI", "display_order": 36},
    {"code": "Shareholder", "category_group": "Shareholder", "display_order": 45},
]


def category_group_for(label: str) -> str | None:
    key = (label or "").strip()
    if not key:
        return None
    for row in CATEGORY_SEEDS:
        if str(row["code"]).casefold() == key.casefold():
            return str(row["category_group"])
    return None


def normalize_mail(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "Pending"
    lowered = raw.casefold()
    if lowered in ("done", "yes", "y", "mail done", "maildone"):
        return "Done"
    if lowered in ("pending", "no", "n", "na", "n/a", "-"):
        return "Pending"
    if "done" in lowered:
        return "Done"
    return raw
