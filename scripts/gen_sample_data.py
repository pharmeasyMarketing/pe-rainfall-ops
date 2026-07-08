"""Generate realistic SAMPLE rainfall data over the REAL pincode geography.

Reads data/pincodes.csv (the geocoded 15k master) and generates synthetic
monsoon data PER GRID CELL (~2.9k cells), exactly mirroring how real mode
works: forecasts are cell-level; pincodes inherit their cell's forecast.

Writes the same gz-CSV schema the real fetchers write, so score.py and
build_site.py are identical across sample and production modes:
  data/forecasts/<run>.csv.gz   (per cell x D0-D6)
  data/observed/<date>.csv.gz   (per pincode; cell truth + local noise)

Model (synthetic but physically plausible for peak SW monsoon):
  * each cell gets a rainfall regime from its lat/lon (west coast, NE, etc.);
  * regimes have wet-day means/probabilities and a day-coherent monsoon pulse;
  * forecast = 31-member ensemble whose skill decays with lead time.

Pure stdlib works; numpy (optional) makes generation ~50x faster.

Run:  python scripts/gen_sample_data.py
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

import common as C

SAMPLE_TODAY = date(2026, 7, 8)
SEED = 20260708
N_MEMBERS = 31

try:
    import numpy as np
    HAVE_NP = True
except ImportError:  # pure-stdlib fallback
    HAVE_NP = False

# regime -> (wet-day mean mm, wet-day probability, long-lead climatology mm)
REGIMES = {
    "WEST_COAST": (38.0, 0.82, 34.0),
    "NORTHEAST": (42.0, 0.85, 36.0),
    "EAST_COAST": (16.0, 0.55, 14.0),
    "CENTRAL": (16.0, 0.60, 14.0),
    "NORTH": (13.0, 0.50, 11.0),
    "SOUTH_INT": (9.0, 0.45, 8.0),
}
REGIME_LIST = list(REGIMES)


def regime_of(lat: float, lon: float) -> str:
    """Crude but geographically sensible monsoon regime from coordinates."""
    if lon >= 88.0 and lat >= 23.0:
        return "NORTHEAST"
    if lat >= 26.0:
        return "NORTH"
    if lat <= 13.5 and lon <= 77.5:
        return "WEST_COAST"          # Kerala / coastal Karnataka
    if lon <= 74.0 and lat <= 23.0:
        return "WEST_COAST"          # Konkan / Goa / south Gujarat coast
    if lat >= 20.0:
        return "CENTRAL"
    if lon >= 79.5:
        return "EAST_COAST"          # TN / AP coastal belt
    return "SOUTH_INT"               # interior peninsula


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def build_pulse(rng, n_days):
    pulse = {}
    for reg in REGIMES:
        phase = rng.uniform(0, 2 * math.pi)
        amp = rng.uniform(0.30, 0.60)
        series, drift = [], 0.0
        for t in range(n_days):
            drift = clamp(0.7 * drift + rng.gauss(0, 0.12), -0.5, 0.5)
            series.append(clamp(1.0 + amp * math.sin(2 * math.pi * t / 8.0 + phase) + drift, 0.35, 1.9))
        pulse[reg] = series
    return pulse


def main():
    C.ensure_dirs()
    rng = random.Random(SEED)

    pincodes = C.load_pincodes()
    # hub cells included even if no delivery pincode shares them
    cells = sorted({p["cell_id"] for p in pincodes} | {h["cell_id"] for h in C.load_hubs()})
    cell_regime = {}
    for cid in cells:
        la, lo = C.cell_center(cid)
        cell_regime[cid] = regime_of(la, lo)
    print(f"[sample] {len(pincodes)} pincodes -> {len(cells)} cells | numpy={'yes' if HAVE_NP else 'no'}")

    # wipe any stale snapshots (schema may have changed between runs)
    for d in (C.FORECASTS, C.OBSERVED):
        for f in C.archive_files(d):
            f.unlink()

    last_valid = SAMPLE_TODAY + timedelta(days=C.MAX_LEAD)
    all_dates = list(C.daterange(C.ARCHIVE_START, last_valid))
    idx = {d: i for i, d in enumerate(all_dates)}
    pulse = build_pulse(rng, len(all_dates))

    # --- "true" daily rain per cell ------------------------------------------
    truth = {}  # (cell, date) -> mm
    for cid in cells:
        mean, wet, _ = REGIMES[cell_regime[cid]]
        for d in all_dates:
            pt = pulse[cell_regime[cid]][idx[d]]
            occ = clamp(wet * (0.40 + 0.60 * pt), 0.0, 0.95)
            if rng.random() < occ:
                amt = rng.gammavariate(1.6, (mean * pt) / 1.6)
            else:
                amt = rng.random() * 1.5
            truth[(cid, d)] = min(amt, 260.0)

    # --- observed feed (per pincode = cell truth x local noise) --------------
    for d in C.daterange(C.ARCHIVE_START, SAMPLE_TODAY - timedelta(days=1)):
        rows = []
        for p in pincodes:
            obs = truth[(p["cell_id"], d)] * math.exp(rng.gauss(0, 0.14))
            rows.append([d, p["pincode"], round(min(obs, 300.0), 1)])
        C.write_csv_gz(C.OBSERVED / f"{d}.csv.gz", ["date", "pincode", "observed_mm"], rows)

    # --- forecast snapshots: per cell, one file per run ----------------------
    np_rng = np.random.default_rng(SEED) if HAVE_NP else None
    climo = ([REGIMES[cell_regime[c]][2] for c in cells])
    for run in C.daterange(C.ARCHIVE_START, SAMPLE_TODAY):
        run_ts = f"{run} 05:00 IST"
        rows = []
        for lead in range(C.MAX_LEAD + 1):
            valid = run + timedelta(days=lead)
            w = clamp(0.92 - 0.13 * lead, 0.08, 0.92)
            k = max(1.1, 3.2 - 0.32 * lead)
            centers = [max(0.2, w * truth[(c, valid)] + (1 - w) * climo[i] * 0.9)
                       for i, c in enumerate(cells)]
            if HAVE_NP:
                cen = np.asarray(centers)
                members = np_rng.gamma(k, cen / k, size=(N_MEMBERS, len(cells)))
                med = np.median(members, axis=0)
                p10 = np.percentile(members, 10, axis=0)
                p90 = np.percentile(members, 90, axis=0)
                p30 = (members >= 30.0).mean(axis=0)
                p60 = (members >= 60.0).mean(axis=0)
                stats = zip(med.tolist(), p10.tolist(), p90.tolist(), p30.tolist(), p60.tolist())
            else:
                def cell_stats(center):
                    ms = sorted(rng.gammavariate(k, center / k) for _ in range(N_MEMBERS))
                    return (ms[N_MEMBERS // 2], ms[max(0, int(.10 * N_MEMBERS))],
                            ms[min(N_MEMBERS - 1, int(.90 * N_MEMBERS))],
                            sum(1 for m in ms if m >= 30) / N_MEMBERS,
                            sum(1 for m in ms if m >= 60) / N_MEMBERS)
                stats = (cell_stats(c) for c in centers)
            for cid, (med_i, p10_i, p90_i, p30_i, p60_i) in zip(cells, stats):
                rows.append([run_ts, cid, lead, valid,
                             round(med_i, 1), round(p10_i, 1), round(p90_i, 1),
                             round(p30_i, 2), round(p60_i, 2),
                             C.band(p30_i, p60_i, med_i), C.lead_class(lead)])
        C.write_csv_gz(C.FORECASTS / f"{run}.csv.gz", C.FORECAST_HEADER, rows)

    n_runs = len(list(C.daterange(C.ARCHIVE_START, SAMPLE_TODAY)))
    print(f"[sample] forecasts {C.ARCHIVE_START} .. {SAMPLE_TODAY} ({n_runs} runs, per-cell gz)")
    print(f"[sample] observed  {C.ARCHIVE_START} .. {SAMPLE_TODAY - timedelta(days=1)} (per-pincode gz)")


if __name__ == "__main__":
    main()
