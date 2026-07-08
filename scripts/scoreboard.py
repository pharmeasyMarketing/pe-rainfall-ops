"""Bake-off scoreboard — score both free backbones against IMERG truth on the
overlapping days, so a switch/pay decision is made on evidence, not vibes.

  Backbone A: NOAA GEFS  (ensemble median, ~27 km, US public domain)
  Backbone B: ECMWF IFS  (HRES deterministic, ~27 km, CC-BY-4.0)

Compared at cell level (cell-mean IMERG) over every (cell, valid_date, lead)
where BOTH forecasts and the observation exist. ECMWF's archive is short, so the
overlap starts thin and grows daily. Writes data/verification/scoreboard.json.

Run:  python scripts/scoreboard.py
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict

import common as C

HEAVY_MM = 30.0      # "heavy day" event
FLAG_MM = 20.0       # a provider "called it wet" if its forecast >= this


def _load(dirpath, valuecol):
    d = {}
    for path in C.archive_files(dirpath):
        with C.open_text(path) as f:
            for r in csv.DictReader(f):
                d[(r["cell_id"], r["valid_date"], int(r["lead_day"]))] = float(r[valuecol])
    return d


def _cell_obs():
    pins = C.load_pincodes()
    cell_of = {p["pincode"]: p["cell_id"] for p in pins}
    acc = defaultdict(list)
    for path in C.archive_files(C.OBSERVED):
        with C.open_text(path) as f:
            for r in csv.DictReader(f):
                acc[(cell_of[r["pincode"]], r["date"])].append(float(r["observed_mm"]))
    return {k: sum(v) / len(v) for k, v in acc.items()}


def _metrics(triples):
    """triples: list of (obs, pred). Return MAE/bias/corr/heavy-recall."""
    n = len(triples)
    if not n:
        return None
    o = [t[0] for t in triples]; p = [t[1] for t in triples]
    mae = sum(abs(a - b) for a, b in zip(p, o)) / n
    bias = sum(b - a for a, b in zip(p, o)) / n
    sx, sy = sum(o), sum(p)
    sxx = sum(a * a for a in o); syy = sum(b * b for b in p); sxy = sum(a * b for a, b in zip(o, p))
    den = math.sqrt(max(1e-9, (n * sxx - sx * sx) * (n * syy - sy * sy)))
    corr = (n * sxy - sx * sy) / den if den else 0.0
    heavy = [(a, b) for a, b in zip(o, p) if a >= HEAVY_MM]
    recall = sum(1 for a, b in heavy if b >= FLAG_MM) / len(heavy) if heavy else None
    return dict(n=n, mae=round(mae, 2), bias=round(bias, 2), corr=round(corr, 3),
                heavy_n=len(heavy), heavy_recall=round(recall, 3) if recall is not None else None)


def main():
    obs = _cell_obs()
    gefs = _load(C.FORECASTS, "rain_mm_median")
    ecmwf = _load(C.ECMWF_FORECASTS, "rain_mm")
    if not ecmwf:
        print("[scoreboard] no ECMWF archive yet — skipping")
        return

    per_lead = defaultdict(lambda: {"gefs": [], "ecmwf": []})
    gefs_wins = ecmwf_wins = ties = 0
    valids = set()
    for key, ec in ecmwf.items():
        cell, valid, lead = key
        gf = gefs.get(key)
        o = obs.get((cell, valid))
        if gf is None or o is None:
            continue
        per_lead[lead]["gefs"].append((o, gf))
        per_lead[lead]["ecmwf"].append((o, ec))
        valids.add(valid)
        dg, de = abs(gf - o), abs(ec - o)
        if de < dg - 0.5:
            ecmwf_wins += 1
        elif dg < de - 0.5:
            gefs_wins += 1
        else:
            ties += 1

    all_g = [t for L in per_lead.values() for t in L["gefs"]]
    all_e = [t for L in per_lead.values() for t in L["ecmwf"]]
    n_cmp = gefs_wins + ecmwf_wins + ties

    board = dict(
        overlap_days=sorted(valids),
        n_days=len(valids),
        n_comparisons=n_cmp,
        providers=dict(
            gefs=dict(name="NOAA GEFS", detail="ensemble median · ~27km · US public domain",
                      overall=_metrics(all_g)),
            ecmwf=dict(name="ECMWF IFS", detail="HRES deterministic · ~27km · CC-BY-4.0",
                       overall=_metrics(all_e)),
        ),
        by_lead={str(L): dict(gefs=_metrics(per_lead[L]["gefs"]),
                              ecmwf=_metrics(per_lead[L]["ecmwf"]))
                 for L in sorted(per_lead)},
        head_to_head=dict(gefs_closer=gefs_wins, ecmwf_closer=ecmwf_wins, ties=ties,
                          gefs_win_pct=round(100 * gefs_wins / n_cmp, 1) if n_cmp else None,
                          ecmwf_win_pct=round(100 * ecmwf_wins / n_cmp, 1) if n_cmp else None),
    )
    C.VERIFICATION.mkdir(parents=True, exist_ok=True)
    (C.VERIFICATION / "scoreboard.json").write_text(json.dumps(board, indent=2), encoding="utf-8")

    g, e = board["providers"]["gefs"]["overall"], board["providers"]["ecmwf"]["overall"]
    print(f"[scoreboard] {n_cmp:,} cell-day comparisons over {len(valids)} overlap days")
    if g and e:
        print(f"  GEFS : MAE {g['mae']}mm bias {g['bias']:+} corr {g['corr']} heavy-recall {g['heavy_recall']}")
        print(f"  ECMWF: MAE {e['mae']}mm bias {e['bias']:+} corr {e['corr']} heavy-recall {e['heavy_recall']}")
        print(f"  head-to-head closer-to-truth: GEFS {board['head_to_head']['gefs_win_pct']}% "
              f"vs ECMWF {board['head_to_head']['ecmwf_win_pct']}%")


if __name__ == "__main__":
    main()
