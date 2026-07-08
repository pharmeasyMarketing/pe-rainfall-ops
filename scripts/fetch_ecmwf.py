"""Second backbone — ECMWF IFS Open Data (HRES), for the free bake-off scoreboard.

ECMWF Open Data is CC-BY-4.0 (commercial use OK with attribution — shown in the
dashboard footer) and free. HRES is deterministic (one run, not an ensemble),
so we store a single forecast mm per cell/lead — enough for a magnitude/skill
comparison against GEFS on IMERG truth.

Writes data/forecasts_ecmwf/<date>.csv.gz [run_ts_ist, cell_id, lead_day,
valid_date, rain_mm]. No backfill — ECMWF's open archive is only a rolling few
days, so the scoreboard accumulates forward from the day this goes live.

VALIDATED 2026-07-08 against ifs/oper 2026-07-07 00z:
  * tp is RUN-TOTAL accumulated (GRIB_stepRange "0-24"), so daily = tp(24(d+1))
    - tp(24d), with tp(step0)=0;
  * units are METERS -> x1000 for mm;
  * 0.25 deg global grid, fetched via Herbie (same stack as GEFS).

    RAINOPS_MODE=real python scripts/fetch_ecmwf.py
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import common as C

PRODUCT = "oper"        # HRES deterministic (00/12z runs reach 240 h)
IST_SHIFT = timedelta(hours=5, minutes=30)


def latest_cycle(now_utc: datetime) -> datetime:
    """Latest 00/12z ECMWF cycle likely fully published (~9 h behind)."""
    t = now_utc - timedelta(hours=9)
    hour = 12 if t.hour >= 12 else 0
    return t.replace(hour=hour, minute=0, second=0, microsecond=0)


def valid0_of(cycle: datetime) -> date:
    """IST calendar date served as D0 — mirrors fetch_forecast so GEFS/ECMWF
    D{lead} target the same valid dates and the scoreboard joins cleanly."""
    return (cycle + timedelta(hours=12) + IST_SHIFT).date()


def fetch_cell_daily(cycle: datetime) -> dict:
    """{cell_id: [day0_mm, ..., day6_mm]} for one ECMWF cycle."""
    from herbie import Herbie
    import numpy as np

    cells = {cid: C.cell_center(cid)
             for cid in sorted({p["cell_id"] for p in C.load_pincodes()}
                               | {h["cell_id"] for h in C.load_hubs()})}
    cell_ids = list(cells)
    lats = np.array([cells[c][0] for c in cell_ids])
    lons_raw = np.array([cells[c][1] for c in cell_ids])

    steps = [24 * d for d in range(1, C.MAX_LEAD + 2)]   # 24,48,...,168
    acc = {0: np.zeros(len(cell_ids))}
    lon_key = None
    for fxx in steps:
        H = Herbie(cycle.strftime("%Y-%m-%d %H:%M"), model="ifs",
                   product=PRODUCT, fxx=fxx, verbose=False)
        ds = H.xarray("tp")
        var = "tp" if "tp" in ds.data_vars else list(ds.data_vars)[0]
        if lon_key is None:
            lon_key = lons_raw % 360 if float(ds.longitude.max()) > 180 else lons_raw
        pts = ds[var].interp(latitude=("points", lats),
                             longitude=("points", lon_key)).values
        acc[fxx] = np.clip(np.asarray(pts, dtype=float) * 1000.0, 0, None)  # m -> mm
        ds.close()

    daily = {}
    for i, cid in enumerate(cell_ids):
        daily[cid] = [max(0.0, float(acc[24 * (d + 1)][i] - acc[24 * d][i]))
                      for d in range(C.MAX_LEAD + 1)]
    print(f"[ecmwf] cycle {cycle:%Y-%m-%d %HZ}: {len(cell_ids)} cells")
    return daily


HEADER = ["run_ts_ist", "cell_id", "lead_day", "valid_date", "rain_mm"]
BACKFILL_DAYS = 6   # ECMWF open-data retains only a rolling few days


def write_run(v0: date, cycle: datetime, run_ts: str) -> None:
    daily = fetch_cell_daily(cycle)
    rows = []
    for cid in sorted(daily):
        for lead in range(C.MAX_LEAD + 1):
            rows.append([run_ts, cid, lead, v0 + timedelta(days=lead),
                         round(daily[cid][lead], 1)])
    C.ECMWF_FORECASTS.mkdir(parents=True, exist_ok=True)
    C.write_csv_gz(C.ECMWF_FORECASTS / f"{v0}.csv.gz", HEADER, rows)
    print(f"[ecmwf] wrote {v0} from cycle {cycle:%Y-%m-%d %HZ}")


def main():
    cycle = latest_cycle(datetime.utcnow())
    v0 = valid0_of(cycle)
    write_run(v0, cycle, f"{v0} {(datetime.utcnow() + IST_SHIFT):%H:%M} IST")

    # best-effort seed: grab whatever recent days ECMWF still retains, so the
    # scoreboard has real overlap immediately instead of starting empty.
    have = {C.file_date(p) for p in C.archive_files(C.ECMWF_FORECASTS)}
    for i in range(1, BACKFILL_DAYS + 1):
        v = v0 - timedelta(days=i)
        if str(v) in have or v < C.ARCHIVE_START:
            continue
        cyc = datetime(v.year, v.month, v.day, 12) - timedelta(days=1)  # (v-1) 12z
        try:
            write_run(v, cyc, f"{v} 05:00 IST")
        except Exception as e:  # noqa: BLE001
            print(f"[ecmwf] backfill {v} unavailable ({type(e).__name__}) — beyond archive")


if __name__ == "__main__":
    main()
