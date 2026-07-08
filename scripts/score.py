"""Verification layer: score every archived forecast against observed rain.

Forecast snapshots are per CELL; observed truth is per PINCODE. We score at
pincode level (each pincode inherits its cell's forecast) because that's the
ops-relevant unit — a cell with 40 delivery pincodes matters 40x as much as a
single-pincode cell.

For each lead day, event = observed_mm >= EVENT_MM (30 mm), flagged = band in
{WATCH, ACT}:
  POD  = hits / (hits + misses)
  FAR  = false_alarms / (hits + false_alarms)
  watch_reliability = P(event | flagged)

Writes data/verification/scores.csv + summary.json.

Run:  python scripts/score.py
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict

import common as C


def load_observed() -> dict:
    obs = {}
    for path in C.archive_files(C.OBSERVED):
        with C.open_text(path) as f:
            for r in csv.DictReader(f):
                obs[(r["pincode"], r["date"])] = float(r["observed_mm"])
    return obs


def main():
    pincodes = C.load_pincodes()
    cell_pins = defaultdict(list)
    for p in pincodes:
        cell_pins[p["cell_id"]].append(p["pincode"])

    obs = load_observed()
    stats = {}

    for path in C.archive_files(C.FORECASTS):
        with C.open_text(path) as f:
            for r in csv.DictReader(f):
                flagged = r["band"] in ("WATCH", "ACT")
                lead = int(r["lead_day"])
                valid = r["valid_date"]
                s = stats.setdefault(lead, dict(
                    hits=0, misses=0, fa=0, cn=0, watch_n=0, watch_hit=0))
                for pin in cell_pins.get(r["cell_id"], ()):
                    o = obs.get((pin, valid))
                    if o is None:
                        continue
                    event = o >= C.EVENT_MM
                    if flagged and event:
                        s["hits"] += 1
                    elif not flagged and event:
                        s["misses"] += 1
                    elif flagged and not event:
                        s["fa"] += 1
                    else:
                        s["cn"] += 1
                    if flagged:
                        s["watch_n"] += 1
                        if event:
                            s["watch_hit"] += 1

    by_lead = []
    for lead in sorted(stats):
        s = stats[lead]
        n = s["hits"] + s["misses"] + s["fa"] + s["cn"]
        pod = s["hits"] / (s["hits"] + s["misses"]) if (s["hits"] + s["misses"]) else None
        far = s["fa"] / (s["hits"] + s["fa"]) if (s["hits"] + s["fa"]) else None
        acc = (s["hits"] + s["cn"]) / n if n else None
        rel = s["watch_hit"] / s["watch_n"] if s["watch_n"] else None
        by_lead.append(dict(
            lead=lead, n=n, hits=s["hits"], misses=s["misses"],
            false_alarms=s["fa"], correct_neg=s["cn"],
            pod=pod, far=far, hit_rate=acc,
            watch_n=s["watch_n"], watch_reliability=rel,
        ))

    C.VERIFICATION.mkdir(parents=True, exist_ok=True)
    with open(C.VERIFICATION / "scores.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["lead_day", "n", "hits", "misses", "false_alarms",
                    "correct_neg", "pod", "far", "hit_rate",
                    "watch_n", "watch_reliability"])
        for b in by_lead:
            w.writerow([b["lead"], b["n"], b["hits"], b["misses"],
                        b["false_alarms"], b["correct_neg"],
                        _r(b["pod"]), _r(b["far"]), _r(b["hit_rate"]),
                        b["watch_n"], _r(b["watch_reliability"])])

    summary = dict(
        event_mm=C.EVENT_MM,
        archive_start=str(C.ARCHIVE_START),
        n_scored=sum(b["n"] for b in by_lead),
        by_lead=by_lead,
    )
    with open(C.VERIFICATION / "summary.json", "w", encoding="utf-8", newline="") as f:
        json.dump(summary, f, indent=2)

    print(f"[score] scored {summary['n_scored']} pincode-day pairs across {len(by_lead)} leads")
    for b in by_lead:
        print(f"  D{b['lead']}: POD={_r(b['pod'])} FAR={_r(b['far'])} "
              f"reliability={_r(b['watch_reliability'])} (n={b['n']})")


def _r(x):
    return round(x, 3) if isinstance(x, float) else ""


if __name__ == "__main__":
    main()
