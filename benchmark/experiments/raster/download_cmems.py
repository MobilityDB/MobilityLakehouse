# Fetch the Copernicus Marine wave cube for the belt and the demo day
from pathlib import Path

import config as C

def main():
    import copernicusmarine as cm
    import pandas as pd

    b = C.BELT_LONLAT
    day = pd.Timestamp(C.DAY)
    pad = pd.Timedelta(hours=C.EO_PAD_HOURS)

    t0 = (day - pad).strftime("%Y-%m-%dT%H:%M:%S")
    t1 = (day + pd.Timedelta(days=1) + pad).strftime("%Y-%m-%dT%H:%M:%S")

    out = Path(C.EO_CUBE)
    cm.subset(
        dataset_id=C.CMEMS_WAVE_DATASET,
        variables=[C.CMEMS_WAVE_VAR],
        minimum_longitude=b["lon_min"], maximum_longitude=b["lon_max"],
        minimum_latitude=b["lat_min"], maximum_latitude=b["lat_max"],
        start_datetime=t0,
        end_datetime=t1,
        output_filename=out.name,
        output_directory=out.parent.as_posix(),
        overwrite=True,
    )
    print(f"[cmems] wrote {out} from {C.CMEMS_WAVE_PRODUCT}  ({t0} .. {t1})")
    print("        now run:  python nc_to_tiff.py")

if __name__ == "__main__":
    main()
