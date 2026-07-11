from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


# ---------------------------------------------------------------------------
# Coordinate configuration
# ---------------------------------------------------------------------------
PDF_LAYOUT: dict[str, Any] = {
    "font_name": "Helvetica",
    "font_size": 8.5,
    "header_font_size": 9.5,
    "max_rows_per_slip": 21,
    "draw_sr_no": False,
    "header": {
        "client_name": (68, 731.5),
        "client_code": (405, 731.5),
        "trade_date": (300, 713.0),
        "account_name": (112, 694.5),
        "details_name": (86, 93.7),
        "details_client_code": (116, 78.1),
        "office_name": (358, 93.7),
        "office_date": (384, 78.1),
    },
    "office_use": {
        "name": "Sachin Hemant Shah",
    },
    "table": {
        "first_row_y": 639.5,
        "row_height": 24.095,
        "columns": {
            "sr_no": {"x": 45, "width": 22, "align": "center"},
            "exchange": {"x": 76, "width": 62, "align": "left"},
            "segment": {"x": 152, "width": 58, "align": "left"},
            "symbol": {"x": 214, "width": 155, "align": "left"},
            "side": {"x": 368.5, "width": 45.4, "align": "center"},
            "quantity": {"x": 419.9, "width": 61.7, "align": "right"},
            "rate": {"x": 493.6, "width": 61.7, "align": "right"},
        },
    },
}


COLUMN_ALIASES = {
    "exchange": ("Exchange",),
    "segment": ("Ser/Exp", "Segment"),
    "symbol": ("Symbol", "Scrip/Symbol/Contract", "Scrip Name"),
    "client_code": ("Client", "Client Code"),
    "client_name": ("Client Name", "Account Name"),
    "buy_qty": ("Buy Qty",),
    "buy_rate": ("Buy Avg.", "Buy Avg", "Buy Rate"),
    "sell_qty": ("Sell Qty",),
    "sell_rate": ("Sell Avg.", "Sell Avg", "Sell Rate"),
}


@dataclass(frozen=True)
class TradeEntry:
    client_code: str
    client_name: str
    exchange: str
    segment: str
    symbol: str
    side: str
    quantity: float
    rate: float


@dataclass(frozen=True)
class GeneratedSlip:
    client_code: str
    client_name: str
    trade_date_iso: str
    trade_date_display: str
    pdf_bytes: bytes
    storage_path: str


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(column).strip() for column in df.columns]
    return df


def require_column(df: pd.DataFrame, logical_name: str) -> str:
    for candidate in COLUMN_ALIASES[logical_name]:
        if candidate in df.columns:
            return candidate
    aliases = ", ".join(COLUMN_ALIASES[logical_name])
    raise ValueError(f"Missing required column for {logical_name!r}. Expected one of: {aliases}")


def clean_text(value: Any) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def to_number(value: Any) -> float:
    if pd.isna(value):
        return 0.0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return 0.0
    if math.isnan(number):
        return 0.0
    return number


def format_quantity(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def format_rate(value: float) -> str:
    return f"{value:.2f}"


def infer_trade_date(csv_path: Path) -> str:
    match = re.search(r"(\d{2})(\d{2})(\d{4})", csv_path.stem)
    if match:
        day, month, year = match.groups()
        return f"{day}-{month}-{year}"
    return datetime.now().strftime("%d-%m-%Y")


def infer_trade_date_iso(csv_path: Path) -> str:
    match = re.search(r"(\d{2})(\d{2})(\d{4})", csv_path.stem)
    if match:
        day, month, year = match.groups()
        return f"{year}-{month}-{day}"
    return datetime.now().strftime("%Y-%m-%d")


def trade_date_iso_to_display(iso: str) -> str:
    parsed = datetime.strptime(iso, "%Y-%m-%d")
    return parsed.strftime("%d-%m-%Y")


def trade_date_display_to_iso(display: str) -> str:
    parsed = datetime.strptime(display, "%d-%m-%Y")
    return parsed.strftime("%Y-%m-%d")


def unbundle_trades(df: pd.DataFrame) -> list[TradeEntry]:
    columns = {name: require_column(df, name) for name in COLUMN_ALIASES}
    trades: list[TradeEntry] = []

    for _, row in df.iterrows():
        base = {
            "client_code": clean_text(row[columns["client_code"]]),
            "client_name": clean_text(row[columns["client_name"]]),
            "exchange": clean_text(row[columns["exchange"]]),
            "segment": clean_text(row[columns["segment"]]),
            "symbol": clean_text(row[columns["symbol"]]),
        }
        if not base["client_code"]:
            continue

        buy_qty = to_number(row[columns["buy_qty"]])
        buy_rate = to_number(row[columns["buy_rate"]])
        sell_qty = to_number(row[columns["sell_qty"]])
        sell_rate = to_number(row[columns["sell_rate"]])

        if buy_qty > 0:
            trades.append(TradeEntry(**base, side="B", quantity=buy_qty, rate=buy_rate))
        if sell_qty > 0:
            trades.append(TradeEntry(**base, side="S", quantity=sell_qty, rate=sell_rate))

    return trades


def group_by_client(trades: Iterable[TradeEntry]) -> dict[str, list[TradeEntry]]:
    grouped: dict[str, list[TradeEntry]] = {}
    for trade in trades:
        grouped.setdefault(trade.client_code, []).append(trade)
    return dict(sorted(grouped.items()))


def chunks(items: list[TradeEntry], size: int) -> Iterable[list[TradeEntry]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "UNKNOWN"


def parse_trade_date_partitions(trade_date_iso: str) -> tuple[str, str, str]:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", trade_date_iso.strip())
    if not match:
        raise ValueError(f"trade_date must be YYYY-MM-DD, received {trade_date_iso!r}.")
    year, month, day = match.groups()
    datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
    return year, month, day


def storage_path_for(
    client_code: str,
    trade_date_iso: str,
    broker_id: str | None = None,
) -> str:
    year, month, day = parse_trade_date_partitions(trade_date_iso)
    safe_code = safe_filename(client_code)
    filename = f"{safe_code}_{trade_date_iso}.pdf"
    if broker_id:
        return f"{broker_id.strip()}/{year}/{month}/{day}/{filename}"
    return f"{year}/{month}/{day}/{filename}"


def truncate_to_width(text: str, max_width: float, font_name: str, font_size: float) -> str:
    if stringWidth(text, font_name, font_size) <= max_width:
        return text
    suffix = "..."
    while text and stringWidth(text + suffix, font_name, font_size) > max_width:
        text = text[:-1]
    return text + suffix if text else suffix


def draw_text(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    width: float,
    align: str = "left",
    font_name: str = "Helvetica",
    font_size: float = 8.5,
) -> None:
    value = truncate_to_width(clean_text(text), width, font_name, font_size)
    if align == "right":
        c.drawRightString(x + width, y, value)
    elif align == "center":
        c.drawCentredString(x + (width / 2), y, value)
    else:
        c.drawString(x, y, value)


def build_overlay(
    page_width: float,
    page_height: float,
    client_code: str,
    client_name: str,
    trade_date: str,
    trades: list[TradeEntry],
    part_offset: int,
) -> BytesIO:
    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=(page_width, page_height))
    font_name = PDF_LAYOUT["font_name"]

    header_font_size = PDF_LAYOUT["header_font_size"]
    c.setFont(font_name, header_font_size)
    header = PDF_LAYOUT["header"]
    draw_text(c, client_name, *header["client_name"], 215, font_name=font_name, font_size=header_font_size)
    draw_text(c, client_code, *header["client_code"], 100, font_name=font_name, font_size=header_font_size)
    draw_text(c, trade_date, *header["trade_date"], 100, font_name=font_name, font_size=header_font_size)
    draw_text(c, client_name, *header["account_name"], 250, font_name=font_name, font_size=header_font_size)
    draw_text(c, client_name, *header["details_name"], 190, font_name=font_name, font_size=header_font_size)
    draw_text(c, client_code, *header["details_client_code"], 110, font_name=font_name, font_size=header_font_size)
    draw_text(
        c,
        PDF_LAYOUT["office_use"]["name"],
        *header["office_name"],
        190,
        font_name=font_name,
        font_size=header_font_size,
    )
    draw_text(c, trade_date, *header["office_date"], 110, font_name=font_name, font_size=header_font_size)

    c.setFont(font_name, PDF_LAYOUT["font_size"])
    table = PDF_LAYOUT["table"]
    columns = table["columns"]
    for row_index, trade in enumerate(trades):
        y = table["first_row_y"] - (row_index * table["row_height"])
        row_values = {
            "sr_no": str(part_offset + row_index + 1),
            "exchange": trade.exchange,
            "segment": trade.segment,
            "symbol": trade.symbol,
            "side": trade.side,
            "quantity": format_quantity(trade.quantity),
            "rate": format_rate(trade.rate),
        }
        for key, value in row_values.items():
            if key == "sr_no" and not PDF_LAYOUT["draw_sr_no"]:
                continue
            config = columns[key]
            draw_text(
                c,
                value,
                config["x"],
                y,
                config["width"],
                config["align"],
                font_name,
                PDF_LAYOUT["font_size"],
            )

    c.save()
    packet.seek(0)
    return packet


def render_trade_slip_page(
    template_path: Path,
    client_code: str,
    client_name: str,
    trade_date_display: str,
    trades: list[TradeEntry],
    part_offset: int,
) -> bytes:
    template_reader = PdfReader(str(template_path))
    template_page = template_reader.pages[0]
    page_width = float(template_page.mediabox.width)
    page_height = float(template_page.mediabox.height)

    overlay_pdf = build_overlay(
        page_width,
        page_height,
        client_code,
        client_name,
        trade_date_display,
        trades,
        part_offset,
    )
    overlay_reader = PdfReader(overlay_pdf)
    template_page.merge_page(overlay_reader.pages[0])

    writer = PdfWriter()
    writer.add_page(template_page)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def build_client_slip_pdf(
    template_path: Path,
    client_code: str,
    client_name: str,
    trade_date_display: str,
    trades: list[TradeEntry],
) -> bytes:
    max_rows = PDF_LAYOUT["max_rows_per_slip"]
    parts = list(chunks(trades, max_rows))
    writer = PdfWriter()

    for part_index, part_trades in enumerate(parts, start=1):
        page_bytes = render_trade_slip_page(
            template_path=template_path,
            client_code=client_code,
            client_name=client_name,
            trade_date_display=trade_date_display,
            trades=part_trades,
            part_offset=(part_index - 1) * max_rows,
        )
        page_reader = PdfReader(BytesIO(page_bytes))
        for page in page_reader.pages:
            writer.add_page(page)

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def process_trades_csv(
    file_bytes: bytes,
    template_path: Path,
    trade_date_iso: str,
    original_filename: str | None = None,
    broker_id: str | None = None,
) -> list[GeneratedSlip]:
    if not template_path.exists():
        raise FileNotFoundError(f"PDF template not found: {template_path}")

    df = normalize_columns(pd.read_csv(BytesIO(file_bytes), skipinitialspace=True))
    trades = unbundle_trades(df)
    grouped = group_by_client(trades)
    trade_date_display = trade_date_iso_to_display(trade_date_iso)

    generated: list[GeneratedSlip] = []
    for client_code, client_trades in grouped.items():
        client_name = client_trades[0].client_name
        pdf_bytes = build_client_slip_pdf(
            template_path=template_path,
            client_code=client_code,
            client_name=client_name,
            trade_date_display=trade_date_display,
            trades=client_trades,
        )
        generated.append(
            GeneratedSlip(
                client_code=client_code,
                client_name=client_name,
                trade_date_iso=trade_date_iso,
                trade_date_display=trade_date_display,
                pdf_bytes=pdf_bytes,
                storage_path=storage_path_for(client_code, trade_date_iso, broker_id=broker_id),
            )
        )

    return generated


def generate_trade_slips_to_dir(
    csv_path: Path,
    template_path: Path,
    output_dir: Path,
    trade_date: str | None = None,
) -> list[Path]:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    if not template_path.exists():
        raise FileNotFoundError(f"PDF template not found: {template_path}")

    file_bytes = csv_path.read_bytes()
    if trade_date:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", trade_date):
            trade_date_iso = trade_date
        else:
            trade_date_iso = trade_date_display_to_iso(trade_date)
    else:
        trade_date_iso = infer_trade_date_iso(csv_path)

    slips = process_trades_csv(
        file_bytes=file_bytes,
        template_path=template_path,
        trade_date_iso=trade_date_iso,
        original_filename=csv_path.name,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for slip in slips:
        filename = f"{safe_filename(slip.client_code)}_slip.pdf"
        output_path = output_dir / filename
        output_path.write_bytes(slip.pdf_bytes)
        generated.append(output_path)

    return generated
