"""One-off backfill — now a thin wrapper.

Both fetchers self-heal: fetch_forecast backfills every missing run date since
ARCHIVE_START (GEFS AWS archive, previous-day 12Z cycles) and fetch_observed
pulls every missing IMERG day. So "backfill" is just one real pipeline pass:

    EARTHDATA_TOKEN=… RAINOPS_MODE=real python scripts/pipeline.py

This script is kept for the README's go-live checklist; it simply runs the
same two fetchers then reminds you to score + rebuild.
"""
from __future__ import annotations

import fetch_forecast
import fetch_observed


def main():
    fetch_forecast.main()
    fetch_observed.main()
    print("[backfill] done — run score.py + build_site.py next "
          "(or just scripts/pipeline.py)")


if __name__ == "__main__":
    main()
