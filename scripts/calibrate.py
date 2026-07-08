"""Recalibrate action-band thresholds against the real forecast-vs-observed
archive, per lead day. Writes data/verification/calibration.json.

Runs every pipeline pass, so the bands self-tune as more real data accrues.

Method: bands trigger on the ensemble MEDIAN (mm) — verification showed it
discriminates heavy-rain days far better than P(>=30mm) for this 27 km ensemble.
For each lead we scan median thresholds and pick, per band, the LOWEST threshold
(widest coverage) whose reliability P(observed>=EVENT_MM | flagged) clears a
target — WATCH a wide cheap net (~0.22), ACT a precise trigger (~0.45). If the
target is physically unreachable at that lead, we fall back to the highest
reliability available with a minimum firing rate (never a silently empty band).

  WATCH = median >= watch_median_mm   (soft banner / auto-extend SLA)
  ACT   = median >= act_median_mm     (proactive customer + hub action)

Run:  python scripts/calibrate.py
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict

import common as C

WATCH_REL_TARGET = 0.22
ACT_REL_TARGET = 0.45
WATCH_MIN_FIRE = 0.010          # >= 1.0% of pincode-days
ACT_MIN_FIRE = 0.002            # >= 0.2%
FLOOR_MM = 8.0                  # never flag on drizzle
GRID = [8, 10, 12, 15, 18, 20, 22, 25, 28, 30, 35, 40, 45, 50, 60, 70, 80]


def pick(data, n, target, min_fire, cap=None):
    """Lowest threshold clearing `target` reliability with >= min_fire coverage;
    else the max-reliability threshold meeting min_fire. Returns (thr, rel, pod)."""
    n_events = sum(1 for _, ev in data if ev) or 1
    rows = []
    for t in GRID:
        if cap is not None and t >= cap:
            break
        fl = [ev for m, ev in data if m >= t]
        if not fl:
            continue
        rel = sum(fl) / len(fl)
        fire = len(fl) / n
        pod = sum(fl) / n_events
        rows.append((t, rel, fire, pod))
    eligible = [r for r in rows if r[2] >= min_fire]
    if not eligible:
        eligible = rows or [(FLOOR_MM, 0.0, 0.0, 0.0)]
    hit = [r for r in eligible if r[1] >= target]
    chosen = min(hit, key=lambda r: r[0]) if hit else max(eligible, key=lambda r: r[1])
    return chosen[0], round(chosen[1], 3), round(chosen[3], 3)


def main():
    pins = C.load_pincodes()
    cell_pins = defaultdict(list)
    for p in pins:
        cell_pins[p["cell_id"]].append(p["pincode"])

    obs = {}
    for path in C.archive_files(C.OBSERVED):
        with C.open_text(path) as f:
            for r in csv.DictReader(f):
                obs[(r["pincode"], r["date"])] = float(r["observed_mm"])

    rec = defaultdict(list)          # lead -> [(median_mm, event_bool)]
    dates = set()
    for path in C.archive_files(C.FORECASTS):
        with C.open_text(path) as f:
            for r in csv.DictReader(f):
                lead = int(r["lead_day"])
                med = float(r["rain_mm_median"])
                for pin in cell_pins.get(r["cell_id"], ()):
                    o = obs.get((pin, r["valid_date"]))
                    if o is None:
                        continue
                    rec[lead].append((med, o >= C.EVENT_MM))
                    dates.add(r["valid_date"])

    if not rec:
        print("[calibrate] no scored data yet — keeping default calibration")
        return

    by_lead = {}
    for lead in sorted(rec):
        data = rec[lead]
        n = len(data)
        act_t, act_rel, act_pod = pick(data, n, ACT_REL_TARGET, ACT_MIN_FIRE)
        watch_t, watch_rel, watch_pod = pick(data, n, WATCH_REL_TARGET, WATCH_MIN_FIRE, cap=act_t)
        watch_t = max(FLOOR_MM, min(watch_t, act_t - 2))
        by_lead[str(lead)] = dict(
            watch_median_mm=float(watch_t), act_median_mm=float(act_t),
            watch_reliability=watch_rel, watch_pod=watch_pod,
            act_reliability=act_rel, act_pod=act_pod, n=n,
        )

    fb_lead = by_lead.get("1") or next(iter(by_lead.values()))
    calib = dict(
        generated_from=dict(n_days=len(dates),
                            start=min(dates) if dates else None,
                            end=max(dates) if dates else None),
        event_mm=C.EVENT_MM,
        targets=dict(watch_reliability=WATCH_REL_TARGET, act_reliability=ACT_REL_TARGET),
        method="median-mm threshold per lead, reliability-targeted",
        by_lead=by_lead,
        fallback=dict(watch_median_mm=fb_lead["watch_median_mm"],
                      act_median_mm=fb_lead["act_median_mm"]),
    )
    C.VERIFICATION.mkdir(parents=True, exist_ok=True)
    C.CALIBRATION_JSON.write_text(json.dumps(calib, indent=2), encoding="utf-8")

    print(f"[calibrate] from {len(dates)} days:")
    for lead in sorted(by_lead, key=int):
        b = by_lead[lead]
        print(f"  D{lead}: WATCH>={b['watch_median_mm']:.0f}mm (rel {b['watch_reliability']}, "
              f"POD {b['watch_pod']}) · ACT>={b['act_median_mm']:.0f}mm "
              f"(rel {b['act_reliability']}, POD {b['act_pod']})")


if __name__ == "__main__":
    main()
