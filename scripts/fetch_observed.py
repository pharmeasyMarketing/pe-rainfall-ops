"""REAL observed ingest — NASA GPM IMERG Late daily precipitation.

Writes data/observed/<date>.csv (date, pincode, observed_mm) — the truth source
the verification layer scores forecasts against.

IMERG Late (GPM_3IMERGDL) is 0.1 deg, ~14 h latency, free. Access needs a NASA
Earthdata login; put a bearer token in the EARTHDATA_TOKEN env var / CI secret
(Earthdata profile -> Generate Token).

    pip install requests h5py numpy
    EARTHDATA_TOKEN=... RAINOPS_MODE=real python scripts/pipeline.py

Fills any missing observed day from ARCHIVE_START up to (yesterday) that we don't
already have, so a gap self-heals on the next run.

Robust by construction: granule product version (V07x) and the HDF5 variable
layout are auto-discovered at runtime (the first run against a real granule
showed a bare hard-coded '/Grid/precipitation' path is not reliable), and the
(lon,lat) vs (lat,lon) orientation is detected from array shapes. Units are
mm/day.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import common as C

GRANULE_TMPL = (
    "https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/GPM_3IMERGDL.07/"
    "{y}/{m:02d}/3B-DAY-L.MS.MRG.3IMERG.{y}{m:02d}{d:02d}-S000000-E235959.{ver}.nc4"
)
# IMERG bumps the letter suffix over time; try newest first, cache what works.
VERSION_CANDIDATES = ["V07D", "V07C", "V07B", "V07A"]
_working_version: list = []
_structure_logged: list = []
HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"


def existing_observed_dates():
    return {C.file_date(p) for p in C.archive_files(C.OBSERVED)}


def missing_dates():
    have = existing_observed_dates()
    yesterday = date.today() - timedelta(days=1)
    return [d for d in C.daterange(C.ARCHIVE_START, yesterday)
            if C.iso(d) not in have]


def read_imerg_day(day):
    """Return a callable field(lat, lon) -> mm for the given day."""
    import h5py
    import numpy as np
    import requests

    token = os.environ.get("EARTHDATA_TOKEN")
    if not token:
        raise SystemExit("EARTHDATA_TOKEN not set — needed for IMERG download.")
    versions = _working_version or VERSION_CANDIDATES
    r = None
    for ver in versions:
        url = GRANULE_TMPL.format(y=day.year, m=day.month, d=day.day, ver=ver)
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                         timeout=180, allow_redirects=True)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        if not _working_version:
            _working_version.append(ver)
            print(f"[imerg] using product version {ver}")
        break
    else:
        raise FileNotFoundError(f"no IMERG granule for {day} in {versions}")
    # Guard: a stripped-auth redirect returns an HTML login page (HTTP 200),
    # not a granule. Fail loudly with the first bytes rather than feeding junk
    # to h5py (which would raise a confusing unrelated error).
    if r.content[:8] != HDF5_MAGIC:
        raise ValueError(f"not an HDF5 granule (first bytes {r.content[:24]!r}) "
                         f"— check EARTHDATA_TOKEN + 'NASA GESDISC DATA ARCHIVE' app auth")

    tmp = C.DATA / f"_imerg_{day}.nc4"
    tmp.write_bytes(r.content)
    try:
        with h5py.File(tmp, "r") as h:
            # Auto-discover datasets by basename so we don't hard-code a path
            # that a product revision might move (the V07 daily layout wasn't
            # confirmed until this ran against a real granule).
            paths = {}

            def _collect(name, obj):
                # MUST return None — visititems halts on any non-None return.
                if isinstance(obj, h5py.Dataset):
                    paths.setdefault(name.split("/")[-1].lower(), name)

            h.visititems(_collect)
            if not _structure_logged:
                _structure_logged.append(True)
                print(f"[imerg] datasets found: {sorted(paths)}")
            p_name = paths.get("precipitation") or paths.get("precipitationcal")
            lon_name = paths.get("lon") or paths.get("longitude")
            lat_name = paths.get("lat") or paths.get("latitude")
            if not (p_name and lon_name and lat_name):
                raise KeyError(f"precip/lon/lat not found; datasets={sorted(paths)[:14]}")
            precip = np.squeeze(np.asarray(h[p_name][:], dtype=float))
            glon = np.asarray(h[lon_name][:], dtype=float)
            glat = np.asarray(h[lat_name][:], dtype=float)
    finally:
        tmp.unlink(missing_ok=True)

    precip = np.where(precip < 0, 0.0, precip)          # -9999.9 fill -> 0
    nlon, nlat = len(glon), len(glat)
    if precip.shape == (nlon, nlat):
        lon_first = True
    elif precip.shape == (nlat, nlon):
        lon_first = False
    else:
        raise ValueError(f"precip shape {precip.shape} matches neither "
                         f"(lon={nlon}, lat={nlat}) orientation")

    def field(lat, lon):
        i = int(np.abs(glon - lon).argmin())
        j = int(np.abs(glat - lat).argmin())
        v = precip[i, j] if lon_first else precip[j, i]
        return max(0.0, float(v))
    return field


def main():
    pincodes = C.load_pincodes()
    todo = missing_dates()
    if not todo:
        print("[imerg] observed archive already complete")
        return
    for day in todo:
        try:
            field = read_imerg_day(day)
        except Exception as e:  # noqa: BLE001
            print(f"[imerg] skip {day}: {e}")
            continue
        rows = [[day, p["pincode"], round(field(p["lat"], p["lon"]), 1)]
                for p in pincodes]
        C.write_csv_gz(C.OBSERVED / f"{day}.csv.gz",
                       ["date", "pincode", "observed_mm"], rows)
        print(f"[imerg] wrote observed {day}")


if __name__ == "__main__":
    main()
