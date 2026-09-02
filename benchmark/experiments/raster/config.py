# Configuration for the raster layer, over the Rodby-Puttgarden ferry belt

import os as _os
from pathlib import Path as _Path

CRS_TRIPS = "EPSG:32632"
CRS_EO = "EPSG:4326"

BELT_LONLAT = dict(lon_min=11.00, lon_max=11.60, lat_min=54.40, lat_max=54.75)

DAY = "2026-01-15"
HOUR = 8

CMEMS_WAVE_PRODUCT = "BALTICSEA_ANALYSISFORECAST_WAV_003_010"
CMEMS_WAVE_DATASET = "cmems_mod_bal_wav_anfc_PT1H-i"
CMEMS_WAVE_VAR = "VHM0"

EO_VAR = CMEMS_WAVE_VAR
EO_VAR_LABEL = "significant wave height"
EO_VAR_UNIT = "m"
EO_PAD_HOURS = 1

EO_CUBE = f"data/eo_{DAY}.nc"
EO_TIFF = f"data/eo_{DAY}_{HOUR:02d}00.tif"

MOBILITYDUCK_EXT = _os.environ.get("MOBILITYDUCK_EXT", "")
if not MOBILITYDUCK_EXT:
    raise RuntimeError(
        "MobilityDuck extension path is not configured. Export MOBILITYDUCK_EXT. "
        "rasterValue() exists only in the Extended build."
    )

TRIPS_GLOB = _os.environ.get(
    "TRIPS_GLOB",
    (_Path(__file__).resolve().parents[2]
     / "data/trips/layout_compact/L2s/**/*.parquet").as_posix(),
)
