"""Assemble the dashboard payload + the daily downloadable CSV from the archive.

Bands are recomputed here from the ensemble median + the latest calibration
(data/verification/calibration.json), so a fresh calibration takes effect on the
whole visible dataset immediately — the stored per-file band is ignored.

Outputs (all generated, gitignored — CI rebuilds and deploys them):
  site/data.js               one JS global (day-arrays stored once per cell)
  site/hist/<XX>.json        history shards, fetched on demand per detail drawer
  site/downloads/latest.csv  one row per pincode x 7-day forecast (ops download)

Run:  python scripts/build_site.py
"""
from __future__ import annotations

import csv
import json
import shutil
from collections import defaultdict
from datetime import timedelta

import common as C

HISTORY_DAYS = 30
BAND_CODE = {"NONE": 0, "WATCH": 1, "ACT": 2}
BAND_NAME = ["NONE", "WATCH", "ACT"]


def load_forecasts(calib):
    files = C.archive_files(C.FORECASTS)
    if not files:
        raise SystemExit("No forecast files found. Run the pipeline first.")
    latest_path = files[-1]
    latest_run = C.file_date(latest_path)

    d1_index = {}
    for path in files:
        with C.open_text(path) as f:
            for r in csv.DictReader(f):
                if r["lead_day"] == "1":
                    med = float(r["rain_mm_median"])
                    d1_index[(r["valid_date"], r["cell_id"])] = (
                        med, BAND_CODE[C.band_from_median(med, 1, calib)])

    cell_days = defaultdict(lambda: [None] * (C.MAX_LEAD + 1))
    run_ts = None
    with C.open_text(latest_path) as f:
        for r in csv.DictReader(f):
            run_ts = r["run_ts_ist"]
            lead = int(r["lead_day"])
            med = float(r["rain_mm_median"])
            cell_days[r["cell_id"]][lead] = [
                med, float(r["rain_mm_p10"]), float(r["rain_mm_p90"]),
                float(r["prob_gt30"]), float(r["prob_gt60"]),
                BAND_CODE[C.band_from_median(med, lead, calib)],
            ]
    return latest_run, run_ts, dict(cell_days), d1_index


def load_observed_recent(latest, days=HISTORY_DAYS):
    cutoff = C.parse_date(latest) - timedelta(days=days + 1)
    obs = {}
    for path in C.archive_files(C.OBSERVED):
        if C.parse_date(C.file_date(path)) < cutoff:
            continue
        with C.open_text(path) as f:
            for r in csv.DictReader(f):
                obs[(r["pincode"], r["date"])] = float(r["observed_mm"])
    return obs


def worst72(days):
    order = None
    for d in days[:3]:
        if d is None:
            continue
        key = (d[5], d[3])
        if order is None or key > order[0]:
            order = (key, d)
    return order[1][5] if order else 0


def build_download_csv(pincodes, cell_days, latest_run, run_ts):
    dl_dir = C.SITE / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    header = ["pincode", "area", "state", "lat", "lon", "cell_id",
              "forecast_generated_ist", "worst_band_72h"]
    for L in range(C.MAX_LEAD + 1):
        header += [f"D{L}_date", f"D{L}_rain_mm", f"D{L}_prob_gt30_pct", f"D{L}_band"]
    rows = []
    for p in pincodes:
        days = cell_days.get(p["cell_id"])
        row = [p["pincode"], p["area"], p["state"], p["lat"], p["lon"], p["cell_id"],
               run_ts, BAND_NAME[worst72(days)] if days else ""]
        for L in range(C.MAX_LEAD + 1):
            d = days[L] if days else None
            if d:
                row += [str(C.parse_date(latest_run) + timedelta(days=L)),
                        d[0], round(d[3] * 100), BAND_NAME[d[5]]]
            else:
                row += ["", "", "", ""]
        rows.append(row)
    rows.sort(key=lambda r: r[0])
    with open(dl_dir / "latest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return (dl_dir / "latest.csv").stat().st_size


def main():
    pincodes = C.load_pincodes()
    priority = C.load_priority()
    hubs = C.load_hubs()
    calib = C.load_calibration()
    latest_run, run_ts, cell_days, d1_index = load_forecasts(calib)
    obs = load_observed_recent(latest_run)
    latest = C.parse_date(latest_run)

    # --- history shards ------------------------------------------------------
    hist_dir = C.SITE / "hist"
    if hist_dir.exists():
        shutil.rmtree(hist_dir)
    hist_dir.mkdir(parents=True)
    shards = defaultdict(dict)
    for p in pincodes:
        pin, cell = p["pincode"], p["cell_id"]
        rows = []
        for i in range(HISTORY_DAYS, 0, -1):
            v = latest - timedelta(days=i)
            if v < C.ARCHIVE_START:
                continue
            vs = C.iso(v)
            o = obs.get((pin, vs))
            fc = d1_index.get((vs, cell))
            if o is None or fc is None:
                continue
            rows.append([vs, o, fc[0], fc[1]])
        if rows:
            shards[pin[:2]][pin] = rows
    for prefix, data in shards.items():
        with open(hist_dir / f"{prefix}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))

    ver = {}
    if (C.VERIFICATION / "summary.json").exists():
        ver = json.loads((C.VERIFICATION / "summary.json").read_text(encoding="utf-8"))

    dl_bytes = build_download_csv(pincodes, cell_days, latest_run, run_ts)

    pins = [[p["pincode"], p["area"], p["state"], p["lat"], p["lon"],
             p["cell_id"], 1 if p["pincode"] in priority else 0]
            for p in pincodes]
    hub_rows = [[h["hub_city"], h["cell_id"], h["lat"], h["lon"]] for h in hubs]

    payload = dict(
        run_mode="REAL",
        generated_ist=run_ts,
        archive_start=str(C.ARCHIVE_START),
        event_mm=C.EVENT_MM,
        calibration=calib,
        scope="courier",
        meta=dict(n_pincodes=len(pincodes), n_cells=len(cell_days),
                  latest_run=latest_run, history_days=HISTORY_DAYS),
        verification=ver,
        hubs=hub_rows,
        cells=cell_days,
        pins=pins,
    )

    C.SITE.mkdir(parents=True, exist_ok=True)
    out = C.SITE / "data.js"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("// Auto-generated by scripts/build_site.py — do not edit.\n")
        f.write("window.__RAINOPS__ = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print(f"[build] data.js {out.stat().st_size/1e6:.1f} MB ({len(pins)} pins, "
          f"{len(cell_days)} cells) | download {dl_bytes/1e6:.1f} MB | "
          f"{len(shards)} hist shards | run {latest_run} @ {run_ts}")


if __name__ == "__main__":
    main()
