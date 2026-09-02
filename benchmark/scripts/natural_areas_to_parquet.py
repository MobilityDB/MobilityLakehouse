# Convert the Natural Areas dataset (WKT MULTIPOLYGON, EPSG:32632) to Parquet with
import re, sys
from pathlib import Path
import pandas as pd
import duckdb

BENCH_ROOT = Path(__file__).resolve().parents[1]
SRC  = Path(sys.argv[1]) if len(sys.argv) > 1 else BENCH_ROOT / "natural_areas.csv"
OUT  = BENCH_ROOT / "data" / "natural_areas" / "natural_areas.parquet"
CRS  = "EPSG:32632"

_num = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")

def covering_box(wkt: str):
    nums = _num.findall(wkt)
    xs = list(map(float, nums[0::2]))
    ys = list(map(float, nums[1::2]))
    n_parts = wkt.count(")),((") + 1
    return min(xs), min(ys), max(xs), max(ys), n_parts, len(xs)

def main():
    print(f"reading {SRC}  ({SRC.stat().st_size/1e6:.1f} MB)")
    df = pd.read_csv(SRC)
    print(f"  {len(df)} areas, {len(df.columns)} columns")

    box = df["geom"].map(covering_box)
    df["xmin"], df["ymin"], df["xmax"], df["ymax"], df["n_parts"], df["n_vertices"] = zip(*box)
    df["bbox_area_km2"] = (df.xmax - df.xmin) * (df.ymax - df.ymin) / 1e6
    df["crs"] = CRS

    con = duckdb.connect()
    con.register("na", df)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM na) TO '{OUT.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    print(f"  wrote {OUT}  ({OUT.stat().st_size/1e6:.1f} MB)")

    multi = int((df.n_parts > 1).sum())
    print("\ncovering-box looseness (spatial-free signal)")
    print(f"  areas               : {len(df)}")
    print(f"  multi-part areas    : {multi}  ({100*multi/len(df):.0f}%)  max parts = {int(df.n_parts.max())}")
    print(f"  bbox area km^2      : median {df.bbox_area_km2.median():.2f}  |  max {df.bbox_area_km2.max():.1f}")
    print(f"  vertices per area   : median {int(df.n_vertices.median())}  |  max {int(df.n_vertices.max())}")
    print("  widest boxes (name, parts, bbox_km2):")
    for _, r in df.nlargest(5, "bbox_area_km2")[["name","n_parts","bbox_area_km2"]].iterrows():
        print(f"    {str(r['name'])[:40]:40s}  parts={int(r.n_parts):3d}  bbox={r.bbox_area_km2:.1f} km^2")

if __name__ == "__main__":
    main()
