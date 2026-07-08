"""Shared constants and helpers for the rainfall-ops pipeline.

Pure standard-library, so the calibrate/score/build_site steps run with a bare
Python 3 install (only the GEFS/IMERG fetchers need the requirements.txt stack).
"""
from __future__ import annotations

import csv
import gzip
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FORECASTS = DATA / "forecasts"
ECMWF_FORECASTS = DATA / "forecasts_ecmwf"   # second backbone (bake-off scoreboard)
OBSERVED = DATA / "observed"
VERIFICATION = DATA / "verification"
SITE = ROOT / "site"
PINCODES_CSV = DATA / "pincodes.csv"
PRIORITY_TXT = DATA / "priority_pincodes.txt"
HUBS_CSV = DATA / "hubs.csv"

# Forecast snapshots are stored PER GRID CELL (not per pincode): 15k pincodes
# collapse to ~2.9k cells, and the forecast genuinely is a cell-level object.
# Pincodes inherit their cell's forecast at score/build time via pincodes.csv.
FORECAST_HEADER = [
    "run_ts_ist", "cell_id", "lead_day", "valid_date",
    "rain_mm_median", "rain_mm_p10", "rain_mm_p90",
    "prob_gt30", "prob_gt60", "band", "lead_class",
]

# ---------------------------------------------------------------------------
# Product constants
# ---------------------------------------------------------------------------
# We maintain an append-only archive from this date onward.
ARCHIVE_START = date(2026, 6, 15)

# Forecast backbone grid resolution (NOAA GEFS ~0.25 deg). Adjacent pincodes
# that fall in the same cell share a forecast -> dedupe on cell_id before any
# real API/GRIB fetch.
GRID_DEG = 0.25

# Forecast horizon shown on the dashboard (D0..D6).
MAX_LEAD = 6

ACTIONABLE_MAX_LEAD = 2  # D0-D2 actionable, D3+ directional

# Event definition used by the verification layer (a day "should have been
# flagged" when observed rain meets this threshold).
EVENT_MM = 30.0

# --- Action bands -----------------------------------------------------------
# Bands trigger on the ENSEMBLE MEDIAN (mm), which real verification showed is a
# far better discriminator than P(>=30mm) for this dry-biased 27 km ensemble
# (median>=40mm ~= 50% reliability vs P>=30's ~37% ceiling). Thresholds are
# recalibrated per lead-day from the real archive by scripts/calibrate.py ->
# data/verification/calibration.json. This default is used only before the first
# calibration exists.
CALIBRATION_JSON = VERIFICATION / "calibration.json"
DEFAULT_CALIBRATION = {
    "by_lead": {},
    "fallback": {"watch_median_mm": 18.0, "act_median_mm": 42.0},
    "targets": {"watch_reliability": 0.22, "act_reliability": 0.45},
}


def load_calibration() -> dict:
    if CALIBRATION_JSON.exists():
        try:
            return json.loads(CALIBRATION_JSON.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return DEFAULT_CALIBRATION


def band_from_median(median_mm: float, lead: int, calib: dict) -> str:
    th = calib["by_lead"].get(str(lead)) or calib["fallback"]
    if median_mm >= th["act_median_mm"]:
        return "ACT"
    if median_mm >= th["watch_median_mm"]:
        return "WATCH"
    return "NONE"

IST = timezone(timedelta(hours=5, minutes=30))

# Human labels for lead days.
LEAD_LABELS = {
    0: "Today",
    1: "Tomorrow",
    2: "In 2 days",
    3: "In 3 days",
    4: "In 4 days",
    5: "In 5 days",
    6: "In 6 days",
}


# ---------------------------------------------------------------------------
# Grid / band logic
# ---------------------------------------------------------------------------
def cell_id(lat: float, lon: float) -> str:
    """Snap a point to its forecast grid cell centroid id."""
    cl = round(lat / GRID_DEG) * GRID_DEG
    cn = round(lon / GRID_DEG) * GRID_DEG
    return f"{cl:.2f}_{cn:.2f}"


def band(p30: float, p60: float, median_mm: float) -> str:
    """Action band from ensemble probabilities. ACT takes precedence."""
    if p60 >= P60_ACT or median_mm >= IMD_HEAVY_MM:
        return "ACT"
    if p30 >= P30_WATCH:
        return "WATCH"
    return "NONE"


def lead_class(lead: int) -> str:
    return "actionable" if lead <= ACTIONABLE_MAX_LEAD else "directional"


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------
def ensure_dirs() -> None:
    for d in (DATA, FORECASTS, OBSERVED, VERIFICATION, SITE):
        d.mkdir(parents=True, exist_ok=True)


def load_pincodes() -> list[dict]:
    rows: list[dict] = []
    with open(PINCODES_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["lat"] = float(r["lat"])
            r["lon"] = float(r["lon"])
            rows.append(r)
    return rows


def load_priority() -> set[str]:
    """Top-N pincode membership (unranked) for the dashboard's default view."""
    if not PRIORITY_TXT.exists():
        return set()
    return {ln.strip() for ln in PRIORITY_TXT.read_text(encoding="utf-8").splitlines() if ln.strip()}


def load_hubs() -> list[dict]:
    """Origin hubs, cell-quantized (no exact warehouse pincodes in the repo)."""
    if not HUBS_CSV.exists():
        return []
    with open(HUBS_CSV, newline="", encoding="utf-8") as f:
        return [dict(r, lat=float(r["lat"]), lon=float(r["lon"]))
                for r in csv.DictReader(f)]


def cell_center(cid: str) -> tuple[float, float]:
    la, lo = cid.split("_")
    return float(la), float(lo)


# ---------------------------------------------------------------------------
# gzip-transparent CSV archive IO (files are <date>.csv or <date>.csv.gz)
# ---------------------------------------------------------------------------
def open_text(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, newline="", encoding="utf-8")


def write_csv_gz(path: Path, header: list[str], rows) -> None:
    # mtime=0 -> deterministic bytes: identical content == identical file, so
    # a regeneration that changes nothing produces no git diff (no repo bloat).
    import io
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            with io.TextIOWrapper(gz, encoding="utf-8", newline="") as f:
                w = csv.writer(f, lineterminator="\n")  # LF on every platform
                w.writerow(header)
                w.writerows(rows)


def archive_files(dirpath: Path) -> list[Path]:
    """All snapshot files in a data dir, sorted by date stem."""
    files = list(dirpath.glob("*.csv")) + list(dirpath.glob("*.csv.gz"))
    return sorted(files, key=lambda p: p.name.split(".")[0])


def file_date(path: Path) -> str:
    return path.name.split(".")[0]


def daterange(d0: date, d1: date):
    d = d0
    while d <= d1:
        yield d
        d += timedelta(days=1)


def iso(d: date) -> str:
    return d.isoformat()


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()
