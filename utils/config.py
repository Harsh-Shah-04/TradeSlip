from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"
DATA_SAMPLES_DIR = PROJECT_ROOT / "data" / "samples"
OUTPUT_DIR = PROJECT_ROOT / "output"
SUPABASE_DIR = PROJECT_ROOT / "supabase"

DEFAULT_TEMPLATE_PATH = ASSETS_DIR / "Airan_Blank_TradeSlip.pdf"
# Local sample path only — sample CSVs are gitignored and not shipped publicly.
DEFAULT_SAMPLE_CSV = DATA_SAMPLES_DIR / "daily_positions.csv"
