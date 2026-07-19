"""Parse Client Master Excel: party header blocks with applicant rows underneath."""

from __future__ import annotations

import io
from typing import Any


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    text = str(value).strip()
    # Excel sometimes leaves trailing tabs in DPID cells
    return text.replace("\t", "").strip()


def _is_party_header(col_a: Any, col_b: Any, col_c: Any) -> bool:
    """Party headers are a lone name in column A (no applicant name/PAN beside them)."""
    name = _cell_str(col_a)
    if not name:
        return False
    # Skip obvious header labels
    if name.upper() in {"SR NO", "SR", "S.NO", "APPLICANT NAME", "PAN", "DPID", "PARTY"}:
        return False
    if _cell_str(col_b) or _cell_str(col_c):
        return False
    # Numeric-only cells are serial numbers, not parties
    try:
        float(name.replace(",", ""))
        return False
    except ValueError:
        return True


def parse_client_excel(file_bytes: bytes) -> list[dict[str, str]]:
    """
    Return flat applicant rows:
      {party, name, pan, dpid}
    Category / amounts are ignored (filled later in the app).
    """
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl is required for Excel import. Add openpyxl to requirements."
        ) from exc

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    ws = wb.active
    current_party = ""
    rows: list[dict[str, str]] = []

    for excel_row in ws.iter_rows(values_only=True):
        cells = list(excel_row) if excel_row else []
        while len(cells) < 4:
            cells.append(None)
        col_a, col_b, col_c, col_d = cells[0], cells[1], cells[2], cells[3]

        if _is_party_header(col_a, col_b, col_c):
            current_party = _cell_str(col_a).upper()
            continue

        name = _cell_str(col_b)
        pan = _cell_str(col_c).upper()
        dpid = _cell_str(col_d)
        if not name:
            continue
        if not current_party:
            # Orphan applicant without a party block — skip
            continue

        rows.append(
            {
                "party": current_party,
                "name": name,
                "pan": pan,
                "dpid": dpid,
            }
        )

    wb.close()
    return rows
