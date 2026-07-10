from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.config import DEFAULT_SAMPLE_CSV, DEFAULT_TEMPLATE_PATH, OUTPUT_DIR
from utils.pdf_processor import generate_trade_slips_to_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate client trade slip PDFs from a daily trade ledger CSV."
    )
    parser.add_argument(
        "--csv",
        default=DEFAULT_SAMPLE_CSV,
        type=Path,
        help="Input daily trade CSV file.",
    )
    parser.add_argument(
        "--template",
        default=DEFAULT_TEMPLATE_PATH,
        type=Path,
        help="Blank trade slip PDF template.",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR,
        type=Path,
        help="Directory where populated PDF slips will be written.",
    )
    parser.add_argument(
        "--trade-date",
        default=None,
        help="Trade date (YYYY-MM-DD or dd-mm-yyyy). Defaults to date inferred from CSV filename.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generated = generate_trade_slips_to_dir(
        csv_path=args.csv,
        template_path=args.template,
        output_dir=args.output_dir,
        trade_date=args.trade_date,
    )
    print(f"Generated {len(generated)} trade slip PDF(s) in: {args.output_dir}")


if __name__ == "__main__":
    main()
