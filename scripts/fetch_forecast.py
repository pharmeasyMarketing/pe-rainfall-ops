"""REAL forecast ingest — NOAA GEFS 31-member ensemble via AWS Open Data.

Writes data/forecasts/<run>.csv.gz in the exact PER-CELL schema
gen_sample_data.py produces, so score.py / build_site.py are mode-agnostic
(pincodes inherit their cell's forecast downstream via pincodes.csv).

Pipeline per run:
  1. unique 0.25 deg grid cells = pincode cells + origin-hub cells;
  2. pick the latest complete GEFS cycle;
  3. for each of the 31 members, pull surface APCP (accumulated precip) at 24-hour
     boundaries fxx = 0,24,...,168 and difference to get daily totals per cell;
  4. per cell/day, compute median, p10, p90, P(>30), P(>60) across the 31 members.

Uses Herbie for byte-range GRIB access (only the APCP messages are downloaded).

    pip install herbie-data xarray cfgrib
    RAINOPS_MODE=real python scripts/pipeline.py

--- TWO THINGS TO VALIDATE ON THE FIRST LIVE RUN (they vary by GEFS product) ---
  (A) product / variable string: 0p50 vs 0p25, and the APCP search regex;
  (B) accumulation semantics: GEFS APCP buckets may reset every 6 h rather than
      accumulate from t=0 — verify whether daily total = A(24(d+1)) - A(24d) or a
      sum of 6-hourly buckets. The DAILY_FROM_RUN_TOTAL flag toggles this.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta

import common as C

N_MEMBERS = 31                    # gec00 + gep01..gep30
GEFS_PRODUCT = "atmos.5"          # 0.5 deg; use "atmos.25" for 0.25 deg if needed
APCP_SEARCH = ":APCP:surface"
DAILY_FROM_RUN_TOTAL = True       # see note (B) above


def unique_cells():
    """cell_id -> (lat, lon) cell centers: pincode cells + origin-hub cells."""
    ids = {p["cell_id"] for p in C.load_pincodes()}
    ids |= {h["cell_id"] for h in C.load_hubs()}
    return {cid: C.cell_center(cid) for cid in sorted(ids)}


def latest_cycle(now_utc):
    """Most recent GEFS cycle likely to be fully available (~5-6 h old)."""
    t = now_utc - timedelta(hours=6)
    hour = (t.hour // 6) * 6
    return t.replace(hour=hour, minute=0, second=0, microsecond=0)


def member_names():
    return ["c00"] + [f"p{n:02d}" for n in range(1, N_MEMBERS)]


def fetch_cell_daily(cycle):
    """Return {cell_id: {lead: [31 member daily-mm]}} for the given cycle.

    Real implementation with Herbie. Imported lazily so sample mode needs no deps.
    """
    from herbie import Herbie  # noqa: F401
    import numpy as np

    cells = unique_cells()
    cell_ids = list(cells)
    lats = np.array([cells[c][0] for c in cell_ids])
    lons = np.array([cells[c][1] for c in cell_ids]) % 360  # GEFS uses 0..360

    # accumulate: daily[cell_id][lead] -> list of member totals
    daily = {c: {d: [] for d in range(C.MAX_LEAD + 1)} for c in cell_ids}

    for m in member_names():
        # run-total accumulation at each 24 h boundary, per cell
        acc = {}  # fxx_hours -> np.array over cells
        needed = [24 * d for d in range(C.MAX_LEAD + 2)]  # 0,24,...,168
        for fxx in needed:
            if fxx == 0:
                acc[0] = np.zeros(len(cell_ids))
                continue
            H = Herbie(cycle.strftime("%Y-%m-%d %H:%M"), model="gefs",
                       member=m, fxx=fxx, product=GEFS_PRODUCT)
            ds = H.xarray(APCP_SEARCH)
            var = [v for v in ds.data_vars][0]
            pts = ds[var].interp(
                latitude=("points", lats), longitude=("points", lons)).values
            acc[fxx] = np.asarray(pts, dtype=float)
        for d in range(C.MAX_LEAD + 1):
            if DAILY_FROM_RUN_TOTAL:
                day_vals = acc[24 * (d + 1)] - acc[24 * d]
            else:
                day_vals = acc[24 * (d + 1)]
            day_vals = np.clip(day_vals, 0, None)
            for i, c in enumerate(cell_ids):
                daily[c][d].append(float(day_vals[i]))
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


def snapshot_rows(run_date, run_ts, daily):
    """Per-cell forecast rows in the shared archive schema."""
    rows = []
    for cid in sorted(daily):
        for lead in range(C.MAX_LEAD + 1):
            med, p10, p90, p30, p60 = summarize(daily[cid][lead])
            valid = run_date + timedelta(days=lead)
            rows.append([run_ts, cid, lead, valid,
                         round(med, 1), round(p10, 1), round(p90, 1),
                         round(p30, 2), round(p60, 2),
                         C.band(p30, p60, med), C.lead_class(lead)])
    return rows


def main():
    now = datetime.utcnow()
    cycle = latest_cycle(now)
    run_date = (cycle + timedelta(hours=5, minutes=30)).date()  # IST calendar day
    run_ts = (cycle + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M IST")

    daily = fetch_cell_daily(cycle)
    out = C.FORECASTS / f"{run_date}.csv.gz"
    C.write_csv_gz(out, C.FORECAST_HEADER, snapshot_rows(run_date, run_ts, daily))
    print(f"[gefs] wrote {out} from cycle {cycle:%Y-%m-%d %HZ} "
          f"({len(daily)} cells, {N_MEMBERS} members)")


if __name__ == "__main__":
    main()
