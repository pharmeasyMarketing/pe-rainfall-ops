# Rainfall Ops — Pincode Delivery Forecast Dashboard

A daily-updated dashboard showing **7-day rainfall risk across all 15,109 serviceable courier
pincodes** (collapsed to 2,928 forecast grid cells) plus the **12 origin hubs**, for the PharmEasy
medicine-delivery team — so ops can pre-emptively extend delivery windows, re-route, and warn
customers before rain disrupts last-mile delivery. Default view: the top-500 priority pincodes;
search covers everything.

Static site (GitHub Pages) + a Python pipeline on GitHub Actions. **No servers, no database** —
the repo's `data/` folder *is* the append-only archive, maintained from **2026-06-15**
(~250 KB/day of gzipped CSV).

> **Currently running on SAMPLE data** — real pincode geography (GeoNames-geocoded), synthetic
> monsoon rain — so the whole thing is visible and clickable before the live feeds are wired in.
> Flip one switch to go live — see [Going live](#going-live). The dashboard shows a ⚠ SAMPLE
> banner until then.

---

## The accuracy contract (read this first)

No vendor sells a true pincode-level forecast — every model runs on a ~9–27 km grid, and monsoon
convective rain is genuinely hard beyond ~2 days. So instead of pretending point accuracy, the
dashboard earns the delivery team's trust three ways:

1. **Probability bands, not a single mm number** — "80% chance of >30 mm" survives forecast error.
2. **Lead-time honesty** — D0–D2 are marked *actionable*, D3–D6 *directional*.
3. **A verification / trust panel** — every past forecast is scored against what actually fell, so
   the team sees exactly how reliable a day-1 vs a day-3 call has been.

Action bands: **WATCH** = P(≥30 mm/24h) ≥ 40% · **ACT** = P(≥60 mm/24h) ≥ 40% or IMD-heavy
(64.5 mm). Thresholds are illustrative — recalibrate against real delivery-slippage after ~6 weeks.

---

## Quickstart (sample data, no dependencies)

Needs only Python 3.9+ — the sample pipeline is pure standard library.

```bash
# from the repo root
python scripts/pipeline.py          # generate sample data -> score -> build site/data.js
```

Then view the dashboard:

```bash
python -m http.server 8765 --directory site
# open http://localhost:8765
```

(Opening `site/index.html` by double-click usually works too, but a local server is reliable
across browsers.)

---

## How it works

```
GitHub Actions (cron 2x/day: 05:00 & 16:30 IST)
  └─ scripts/pipeline.py
       ├─ [real]  fetch_forecast.py  → NOAA GEFS 31-member ensemble (AWS Open Data, public domain)
       ├─ [real]  fetch_observed.py  → NASA GPM IMERG Late (truth source, ~1-day lag)
       ├─ [sample] gen_sample_data.py → synthetic monsoon data (no network/credentials)
       ├─ score.py       → POD / FAR / reliability by lead day  → data/verification/
       └─ build_site.py  → site/data.js  (one JS global the dashboard reads)
  └─ commit data/ + site/  → GitHub Pages redeploys
```

**Why GEFS (free) not a paid API:** it's US public domain (cleanest possible licence for internal
commercial use), it's a real 31-member ensemble (so the probability bands are honest, not derived),
and its AWS archive lets us backfill *forecasts as issued* since June 15. Trade-off: ~27 km grid
vs a paid ~9 km blend — which matters less than it sounds for convective rain, and is exactly what
the Phase-2 vulnerability layer is designed to overcome (rationale in the internal plan doc,
kept outside this repo).

## Repo layout

```
rainfall-ops/
├─ .github/workflows/daily.yml     # 2x/day refresh + Pages deploy
├─ data/
│  ├─ pincodes.csv                 # 15,109 rows: pincode, area, state, lat, lon, cell_id, geo_source
│  ├─ priority_pincodes.txt        # top-500 membership, alphabetized (no volumes shipped)
│  ├─ hubs.csv                     # 12 origin hubs, cell-quantized (no exact warehouse pincodes)
│  ├─ forecasts/YYYY-MM-DD.csv.gz  # one snapshot per run: CELL × D0-D6 (~190 KB)
│  ├─ observed/YYYY-MM-DD.csv.gz   # actual rain per PINCODE per day (truth, ~60 KB)
│  └─ verification/                # scores.csv + summary.json
├─ data-private/                   # gitignored: raw dumps, volumes, breach rates
├─ scripts/                        # pipeline, fetchers, scorer, site builder
└─ site/                           # Pages root — index.html, app.js
                                   # data.js + hist/ are GENERATED (gitignored; CI rebuilds)
```

**Why per-cell storage:** every forecast model runs on a grid; all pincodes inside one 0.25° cell
genuinely share a forecast. Storing per cell keeps snapshots ~5× smaller and makes the pincode →
cell join explicit (via `pincodes.csv`). The dashboard payload does the same trick — day-arrays
once per cell — which is how 15k pincodes fit in a ~1.6 MB `data.js`.

## Data schema

`data/forecasts/*.csv.gz` (per cell):

| col | meaning |
|---|---|
| `run_ts_ist` | when this snapshot was issued |
| `cell_id` | 0.25° grid cell (e.g. `19.00_72.75`) |
| `lead_day`, `valid_date` | D0…D6 and the date it targets |
| `rain_mm_median`, `_p10`, `_p90` | ensemble median + p10–p90 spread |
| `prob_gt30`, `prob_gt60` | P(≥30 mm), P(≥60 mm) across members |
| `band`, `lead_class` | NONE/WATCH/ACT · actionable/directional |

The Phase-2 **`vulnerability_score`** is a per-*pincode* quantity — it joins onto `pincodes.csv`
(trained from delivery-slippage history), not onto the forecast files.

---

## Going live

1. ~~Add pincodes~~ **Done** — all 15,109 courier pincodes are loaded and geocoded (97.3% direct
   GeoNames match; 409 patched via pincode-prefix neighbours — see `geo_source` column). To
   improve centroids later, replace lat/lon with the mean of PharmEasy's own geocoded delivery
   addresses per pincode.
2. ~~NASA Earthdata token~~ **Done** — repo secret `EARTHDATA_TOKEN` set (regenerate yearly at
   urs.earthdata.nasa.gov; requires the "NASA GESDISC DATA ARCHIVE" app authorized).
3. ~~Switch mode~~ **Done** — repo variable `RAINOPS_MODE=real`.
4. **Backfill is automatic:** both fetchers self-heal — every scheduled run fills any missing
   archive dates since June 15 (GEFS: previous-day 12Z cycles; IMERG: every missing day), under a
   time budget (`RAINOPS_BACKFILL_BUDGET_MIN`, default 240) so a big backlog spreads safely
   across runs.
5. ~~Validate GEFS semantics~~ **Done 2026-07-08:** GEFS APCP confirmed as 6-hour buckets
   (`GRIB_stepRange "6-12"` at f012) — daily totals are bucket sums, and the fetcher re-detects
   the semantics at runtime so a future NCEP change fails loudly, not silently. IMERG granule
   version is auto-discovered (V07D→V07A).

## Credits / attribution

Forecast: NOAA GEFS (US public domain) · Observed truth: NASA GPM IMERG · Pincode centroids:
[GeoNames](https://www.geonames.org/) postal dataset (CC BY 4.0) · Historical training (Phase 2):
CHIRPS. Attribution for GeoNames is required and shown in the dashboard footer.

## Extending / bake-off

`score.py` is also the **vendor bake-off harness**: point it at a Skymet / Google / Tomorrow.io
trial feed in the same CSV schema and it ranks every provider on identical IMERG ground truth. Any
future case for *paying* for data gets made with these numbers, not vendor claims.

## Phase 2 (parked, schema-ready): the pincode vulnerability layer

At a 27 km grid, two adjacent pincodes share a forecast but not a waterlogging profile — one slips
at 40 mm, the other absorbs 80 mm. Phase 2 trains a per-pincode **`vulnerability_score`** from
**CHIRPS (~5 km, 1981–present)** rainfall joined to PharmEasy's delivery-slippage history, and
upgrades the ACT band from `forecast probability` to `forecast probability × vulnerability`. The
schema and scorer are already built for it — it's a drop-in, not a migration. (Full rationale in
the internal plan doc, kept outside this repo.)
