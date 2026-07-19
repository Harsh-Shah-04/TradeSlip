from __future__ import annotations

# Trade Category (main) + Sub-Category (depends on Category).

TRADE_CATEGORIES: list[str] = [
    "IPO Application",
    "Premium",
    "Subject 2",
]

# Shared application-style sub-categories for IPO Application and Subject 2
APPLICATION_SUB_CATEGORIES: list[str] = [
    "15K",
    "2-",
    "2+",
    "10+",
    "15K Shareholder",
    "2- Shareholder",
]

PREMIUM_SUB_CATEGORIES: list[str] = [
    "Share",
]

SUB_CATEGORIES_BY_CATEGORY: dict[str, list[str]] = {
    "IPO Application": APPLICATION_SUB_CATEGORIES,
    "Subject 2": APPLICATION_SUB_CATEGORIES,
    "Premium": PREMIUM_SUB_CATEGORIES,
}

# Legacy Excel / Phase-1 shorthands → new Sub-Category labels
LEGACY_SUB_CATEGORY_MAP: dict[str, str] = {
    "15K": "15K",
    "2+": "2+",
    "10+": "10+",
    "2-": "2-",
    "2-SHARE": "2-",
    "2- SHARE": "2-",
    "15K (SHAREHOLDER)": "15K Shareholder",
    "15K SHAREHOLDER": "15K Shareholder",
    "15K (Shareholder)": "15K Shareholder",
    "SHARE": "2- Shareholder",
    "2- SHAREHOLDER": "2- Shareholder",
    "2-Shareholder": "2- Shareholder",
    "Retail": "15K",
    "Small HNI": "2+",
    "sHNI": "2+",
    "Big HNI": "10+",
    "bHNI": "10+",
    "Shareholder": "2- Shareholder",
}


def is_premium(category: str | None) -> bool:
    return (category or "").strip() == "Premium"


def quantity_label(category: str | None) -> str:
    return "No. of Shares" if is_premium(category) else "BUY APP"


def sell_quantity_label(category: str | None) -> str:
    return "SELL SHARES" if is_premium(category) else "SELL APP"


def normalize_sub_category(raw: str | None) -> str:
    key = (raw or "").strip()
    if not key:
        return ""
    mapped = LEGACY_SUB_CATEGORY_MAP.get(key) or LEGACY_SUB_CATEGORY_MAP.get(key.upper())
    if mapped:
        return mapped
    # Case-insensitive match against known application subs
    for code in APPLICATION_SUB_CATEGORIES + PREMIUM_SUB_CATEGORIES:
        if code.casefold() == key.casefold():
            return code
    return key


def sub_categories_for(category: str | None) -> list[str]:
    return list(SUB_CATEGORIES_BY_CATEGORY.get((category or "").strip(), []))


def validate_category_pair(category: str, sub_category: str) -> tuple[str, str]:
    cat = (category or "").strip()
    if cat not in TRADE_CATEGORIES:
        raise ValueError(
            f"Category must be one of: {', '.join(TRADE_CATEGORIES)}."
        )
    sub = normalize_sub_category(sub_category)
    allowed = sub_categories_for(cat)
    if cat == "Premium":
        sub = "Share"
    elif sub not in allowed:
        raise ValueError(
            f"Sub-Category '{sub_category}' is not valid for {cat}. "
            f"Choose one of: {', '.join(allowed)}."
        )
    return cat, sub


def trade_category_payload() -> dict[str, object]:
    return {
        "categories": TRADE_CATEGORIES,
        "sub_categories": SUB_CATEGORIES_BY_CATEGORY,
        "quantity_labels": {
            "IPO Application": "BUY APP",
            "Subject 2": "BUY APP",
            "Premium": "No. of Shares",
        },
        "sell_quantity_labels": {
            "IPO Application": "SELL APP",
            "Subject 2": "SELL APP",
            "Premium": "SELL SHARES",
        },
    }


# Keep legacy seeds for Client Master applicant optional category hints
CATEGORY_SEEDS: list[dict[str, object]] = [
    {"code": code, "category_group": None, "display_order": i * 10}
    for i, code in enumerate(APPLICATION_SUB_CATEGORIES, start=1)
]


def category_group_for(label: str) -> str | None:
    """Legacy helper — prefer trade Category now; still map sub-category shorthands."""
    sub = normalize_sub_category(label)
    mapping = {
        "15K": "Retail",
        "2-": "Retail",
        "2+": "Small HNI",
        "10+": "Big HNI",
        "15K Shareholder": "Shareholder",
        "2- Shareholder": "Shareholder",
        "Share": "Premium",
    }
    return mapping.get(sub)


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
