"""Single entry point for one dashboard refresh — used by CI and locally.

Mode is chosen by the RAINOPS_MODE env var:
  * "sample" (default) -> regenerate synthetic data (no network/credentials)
  * "real"             -> fetch NOAA GEFS forecast + NASA IMERG observed

Then it always scores and rebuilds the site payload.

  RAINOPS_MODE=sample python scripts/pipeline.py
  RAINOPS_MODE=real   python scripts/pipeline.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common as C  # noqa: E402


def main():
    mode = os.environ.get("RAINOPS_MODE", "sample").lower()
    C.ensure_dirs()
    print(f"[pipeline] mode={mode}")

    if mode == "sample":
        import gen_sample_data
        gen_sample_data.main()
    elif mode == "real":
        import fetch_forecast
        fetch_forecast.main()
        import fetch_observed
        fetch_observed.main()
    else:
        raise SystemExit(f"unknown RAINOPS_MODE={mode!r} (use 'sample' or 'real')")

    import score
    score.main()
    import build_site
    build_site.main()
    print("[pipeline] done")


if __name__ == "__main__":
    main()
