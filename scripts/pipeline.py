"""Single entry point for one dashboard refresh — used by CI and locally.

Real mode only (the sample generator was removed once real feeds went live):
  fetch NOAA GEFS forecast -> fetch NASA IMERG observed -> calibrate bands
  -> score -> rebuild the site payload.

Requires EARTHDATA_TOKEN (IMERG) and the GRIB stack from requirements.txt.

  RAINOPS_MODE=real python scripts/pipeline.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C  # noqa: E402


def main():
    mode = os.environ.get("RAINOPS_MODE", "real").lower()
    if mode != "real":
        raise SystemExit(
            f"RAINOPS_MODE={mode!r} unsupported — this is a live system, real mode only.")
    C.ensure_dirs()
    print("[pipeline] mode=real")

    import fetch_forecast
    fetch_forecast.main()
    import fetch_ecmwf          # second backbone for the bake-off scoreboard
    try:
        fetch_ecmwf.main()
    except Exception as e:      # ECMWF must never block the primary refresh
        print(f"[pipeline] ECMWF fetch failed ({type(e).__name__}: {e}) — continuing")
    import fetch_observed
    fetch_observed.main()
    import calibrate
    calibrate.main()
    import score
    score.main()
    import scoreboard
    scoreboard.main()
    import build_site
    build_site.main()
    print("[pipeline] done")


if __name__ == "__main__":
    main()
