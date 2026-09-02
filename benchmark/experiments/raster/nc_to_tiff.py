# Write one hour of the EO cube out as a GeoTIFF: the GDAL bundled with the

import sys

import rioxarray
import xarray as xr

import config as C

def main(day: str = C.DAY, hour: int = C.HOUR) -> str:
    ds = xr.open_dataset(C.EO_CUBE)
    when = f"{day}T{hour:02d}:00:00"
    da = ds[C.EO_VAR].sel(time=when, method="nearest")
    da = da.sortby("latitude", ascending=False)
    da = da.rio.write_crs(C.CRS_EO).rio.set_spatial_dims("longitude", "latitude")
    out = C.EO_TIFF
    da.rio.to_raster(out)
    print(f"{out}  {float(da.min()):.3f}..{float(da.max()):.3f} {C.EO_VAR_UNIT}"
          f"  at {str(da.time.values)[:19]}Z")
    return out

if __name__ == "__main__":
    main(*(sys.argv[1:] or []))
