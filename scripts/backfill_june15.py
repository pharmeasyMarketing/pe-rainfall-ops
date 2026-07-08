"""One-off backfill of the archive from ARCHIVE_START (2026-06-15) to today.

  * Observed: fetch_observed already self-heals every missing day, so we just
    call it once — it pulls every IMERG day back to ARCHIVE_START.
  * Forecast: re-issues the historical GEFS forecasts *as they were issued* on
    each past run date, so the trust panel reflects real skill from day one.
    GEFS on AWS retains a rolling archive; for dates beyond that window the
    forecast backfill is skipped (observed still lands, so scoring resumes once
    live forecasts accrue).

    pip install herbie-data xarray cfgrib requests h5py numpy
    EARTHDATA_TOKEN=... python scripts/backfill_june15.py
"""
from __future__ import annotations

from datetime import datetime

import common as C
import fetch_forecast as ff
import fetch_observed as fo


def backfill_forecasts():
    from herbie import Herbie  # noqa: F401  (fail fast if deps missing)
    today = datetime.utcnow().date()
    have = {C.file_date(p) for p in C.archive_files(C.FORECASTS)}
    for run in C.daterange(C.ARCHIVE_START, today):
        if str(run) in have:
            continue
        cycle = datetime(run.year, run.month, run.day, 0)  # 00Z of that day
        try:
            daily = ff.fetch_cell_daily(cycle)
        except Exception as e:  # noqa: BLE001
            print(f"[backfill] forecast {run} unavailable ({e}) — skipping")
            continue
        run_ts = f"{run} 05:00 IST"
        C.write_csv_gz(C.FORECASTS / f"{run}.csv.gz", C.FORECAST_HEADER,
                       ff.snapshot_rows(run, run_ts, daily))
        print(f"[backfill] wrote forecast {run}")


def main():
    print("[backfill] observed …")
    fo.main()
    print("[backfill] forecasts …")
    backfill_forecasts()
    print("[backfill] done — run score.py + build_site.py next")


if __name__ == "__main__":
    main()
