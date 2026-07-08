"""REAL forecast ingest — NOAA GEFS 31-member ensemble via AWS Open Data.

Writes data/forecasts/<date>.csv.gz in the PER-CELL schema shared with sample
mode. Also SELF-HEALS the archive: any missing run date since ARCHIVE_START is
backfilled from the GEFS AWS archive with the cycle that would have been
available at a real 05:00 IST refresh (previous day's 12Z).

VALIDATED 2026-07-08 against gefs.20260707/12 on AWS:
  * APCP arrives as 6-HOUR BUCKETS (GRIB_stepRange "6-12" at fxx=12), so a
    day's total = sum of four 6-h buckets — NOT a difference of 24 h
    boundaries. Detected at runtime anyway (see detect_semantics) and the
    fetch strategy branches, so a future NCEP product change degrades loudly,
    not silently.
  * variable decodes as 'tp', units kg m-2 (== mm); grid 0.5 deg, lon 0-360.

Day windows are mapped to IST calendar dates by window center: a 12Z cycle's
hours 0-24 center at 12Z+12h+5:30 = 05:30 IST next day, so D0 of the morning
refresh is "today in IST" — matching what the delivery team means by "today".

    pip install herbie-data xarray cfgrib eccodes numpy
    RAINOPS_MODE=real python scripts/pipeline.py
"""
from __future__ import annotations

import os
import statistics
import time
from datetime import date, datetime, timedelta

import common as C

N_MEMBERS = 31                    # members 0 (control) + 1..30
GEFS_PRODUCT = "atmos.5"          # pgrb2a 0.5 deg, 6-hourly to 384 h
APCP_SEARCH = ":APCP:"
STEP_H = 6
IST_SHIFT = timedelta(hours=5, minutes=30)


def unique_cells():
    """cell_id -> (lat, lon) cell centers: pincode cells + origin-hub cells."""
    ids = {p["cell_id"] for p in C.load_pincodes()}
    ids |= {h["cell_id"] for h in C.load_hubs()}
    return {cid: C.cell_center(cid) for cid in sorted(ids)}


def latest_cycle(now_utc: datetime) -> datetime:
    """Most recent GEFS cycle likely to be fully on AWS (~6 h behind)."""
    t = now_utc - timedelta(hours=6)
    return t.replace(hour=(t.hour // 6) * 6, minute=0, second=0, microsecond=0)


def valid0_of(cycle: datetime) -> date:
    """IST calendar date served as D0 (center of the cycle's first 24 h)."""
    return (cycle + timedelta(hours=12) + IST_SHIFT).date()


def detect_semantics(cycle: datetime) -> str:
    """'buckets' (6-h accumulations) or 'runtotal' (0-fxx accumulations)."""
    from herbie import Herbie
    H = Herbie(cycle.strftime("%Y-%m-%d %H:%M"), model="gefs",
               product=GEFS_PRODUCT, member=1, fxx=2 * STEP_H)
    ds = H.xarray(APCP_SEARCH)
    var = list(ds.data_vars)[0]
    rng = str(ds[var].attrs.get("GRIB_stepRange", ""))
    mode = "runtotal" if rng.startswith("0-") else "buckets"
    print(f"[gefs] APCP stepRange@f{2*STEP_H:03d} = {rng!r} -> {mode}")
    return mode


def fetch_cell_daily(cycle: datetime, mode: str | None = None) -> dict:
    """{cell_id: {lead: [member daily-mm, ...]}} for one cycle.

    Members with any missing/corrupt message are dropped whole (ensemble
    shrinks rather than mixing partial days).
    """
    from herbie import Herbie
    import numpy as np

    if mode is None:
        mode = detect_semantics(cycle)

    cells = unique_cells()
    cell_ids = list(cells)
    lats = np.array([cells[c][0] for c in cell_ids])
    lons = np.array([cells[c][1] for c in cell_ids]) % 360  # GEFS lon 0..360

    horizon_h = (C.MAX_LEAD + 1) * 24
    if mode == "buckets":
        fxx_list = list(range(STEP_H, horizon_h + 1, STEP_H))
    else:
        fxx_list = [24 * d for d in range(1, C.MAX_LEAD + 2)]

    daily = {c: {d: [] for d in range(C.MAX_LEAD + 1)} for c in cell_ids}
    kept = 0
    for m in range(N_MEMBERS):
        vals = {}  # fxx -> np.array over cells
        try:
            for fxx in fxx_list:
                H = Herbie(cycle.strftime("%Y-%m-%d %H:%M"), model="gefs",
                           product=GEFS_PRODUCT, member=m, fxx=fxx, verbose=False)
                ds = H.xarray(APCP_SEARCH)
                var = list(ds.data_vars)[0]
                pts = ds[var].interp(
                    latitude=("points", lats), longitude=("points", lons)).values
                vals[fxx] = np.clip(np.asarray(pts, dtype=float), 0, None)
                ds.close()
        except Exception as e:  # noqa: BLE001
            print(f"[gefs] member {m:02d} dropped ({type(e).__name__}: {e})")
            continue
        for d in range(C.MAX_LEAD + 1):
            if mode == "buckets":
                day = sum(vals[f] for f in range(24 * d + STEP_H, 24 * (d + 1) + 1, STEP_H))
            else:
                prev = vals.get(24 * d)
                day = vals[24 * (d + 1)] - (prev if prev is not None else 0.0)
            day = np.clip(day, 0, None)
            for i, c in enumerate(cell_ids):
                daily[c][d].append(float(day[i]))
        kept += 1
    if kept < min(8, N_MEMBERS):
        raise RuntimeError(f"only {kept}/{N_MEMBERS} members usable for {cycle:%Y-%m-%d %HZ}")
    print(f"[gefs] cycle {cycle:%Y-%m-%d %HZ}: {kept}/{N_MEMBERS} members, {len(cell_ids)} cells")
    return daily


def summarize(vals):
    vals = sorted(vals)
    n = len(vals)
    median = statistics.median(vals)
    p10 = vals[max(0, int(0.10 * n))]
    p90 = vals[min(n - 1, int(0.90 * n))]
    p30 = sum(1 for v in vals if v >= 30.0) / n
    p60 = sum(1 for v in vals if v >= 60.0) / n
    return median, p10, p90, p30, p60


def snapshot_rows(run_date: date, run_ts: str, daily: dict) -> list:
    rows = []
    for cid in sorted(daily):
        for lead in range(C.MAX_LEAD + 1):
            med, p10, p90, p30, p60 = summarize(daily[cid][lead])
            rows.append([run_ts, cid, lead, run_date + timedelta(days=lead),
                         round(med, 1), round(p10, 1), round(p90, 1),
                         round(p30, 2), round(p60, 2),
                         C.band(p30, p60, med), C.lead_class(lead)])
    return rows


def write_run(run_date: date, cycle: datetime, daily: dict, stamp: str) -> None:
    C.write_csv_gz(C.FORECASTS / f"{run_date}.csv.gz", C.FORECAST_HEADER,
                   snapshot_rows(run_date, stamp, daily))
    print(f"[gefs] wrote {run_date} from cycle {cycle:%Y-%m-%d %HZ}")


def main():
    now = datetime.utcnow()
    cycle = latest_cycle(now)
    mode = detect_semantics(cycle)

    # --- live refresh (always overwrite today's file with the freshest cycle)
    v0 = valid0_of(cycle)
    stamp = f"{v0} {(now + IST_SHIFT):%H:%M} IST"
    write_run(v0, cycle, fetch_cell_daily(cycle, mode), stamp)

    # --- self-heal: backfill any missing archive date since ARCHIVE_START ----
    # Budgeted so a big backlog spreads safely across scheduled runs instead of
    # risking the 6 h Actions job limit; each run commits its progress.
    budget_min = float(os.environ.get("RAINOPS_BACKFILL_BUDGET_MIN", "45"))
    t0 = time.monotonic()
    have = {C.file_date(p) for p in C.archive_files(C.FORECASTS)}
    missing = [d for d in C.daterange(C.ARCHIVE_START, v0 - timedelta(days=1))
               if str(d) not in have]
    if missing:
        print(f"[gefs] self-heal: {len(missing)} missing run dates "
              f"(budget {budget_min:.0f} min)")
    for run_date in missing:
        if (time.monotonic() - t0) / 60 > budget_min:
            print(f"[gefs] backfill budget reached — remaining dates continue next run")
            break
        # cycle a real 05:00 IST refresh would have used: previous day's 12Z
        cyc = datetime(run_date.year, run_date.month, run_date.day, 12) - timedelta(days=1)
        try:
            daily = fetch_cell_daily(cyc, mode)
        except Exception as e:  # noqa: BLE001
            print(f"[gefs] backfill {run_date} unavailable ({e}) — skipping")
            continue
        write_run(run_date, cyc, daily, f"{run_date} 05:00 IST")


if __name__ == "__main__":
    main()
