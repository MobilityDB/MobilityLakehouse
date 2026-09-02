# Build L0X / L0Z / L0H: daily L0, rewritten with rows sorted in-file (lexicographic /
import os, shutil, time
from pathlib import Path
import duckdb

from lakehouse.store import s3io
from lakehouse.query.registry import TRIPS_DEST

N = 1 << 16
CELL = float(os.getenv("D_CELL_M", "5000"))
RG = int(os.getenv("D_ROW_GROUP", "1024"))
_T = TRIPS_DEST.rstrip("/")
L0_ROOT = f"{_T}/L0/L0"
CEN = "(xmin+xmax)/2.0, (ymin+ymax)/2.0"
_COLS = "mmsi, ship_type, segment_type, traj, tmin, tmax, xmin, xmax, ymin, ymax, dt"
OUT = {m: os.getenv(f"D_OUT_{m}", f"{_T}/layouts_daily/{m}") for m in ("L0X", "L0Z", "L0H")}

def morton(ix, iy):
    def _p(n):
        n &= 0xFFFF
        n = (n | (n << 8)) & 0x00FF00FF
        n = (n | (n << 4)) & 0x0F0F0F0F
        n = (n | (n << 2)) & 0x33333333
        n = (n | (n << 1)) & 0x55555555
        return n
    return _p(ix) | (_p(iy) << 1)

def xy2d(n, x, y):
    d = 0; s = n // 2
    while s > 0:
        rx = 1 if (x & s) > 0 else 0
        ry = 1 if (y & s) > 0 else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x; y = s - 1 - y
            x, y = y, x
        s //= 2
    return d

def _reset(out):
    if s3io.is_s3(out):
        s3io.clear_prefix(out)
    else:
        p = Path(out); shutil.rmtree(p, ignore_errors=True); p.mkdir(parents=True)

def _list(root):
    return s3io.list_parquet(root) if s3io.is_s3(root) \
        else [p.as_posix() for p in sorted(Path(root).rglob("*.parquet"))]

def main() -> None:
    c = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    c.execute("SET TimeZone='UTC';")
    c.execute("SET threads=4")
    if any(s3io.is_s3(p) for p in (TRIPS_DEST, *OUT.values())):
        s3io.attach_s3(c)

    x0, x1, y0, y1 = c.execute(f"""
        SELECT quantile_cont((xmin+xmax)/2, 0.005), quantile_cont((xmin+xmax)/2, 0.995),
               quantile_cont((ymin+ymax)/2, 0.005), quantile_cont((ymin+ymax)/2, 0.995)
        FROM read_parquet('{L0_ROOT}/**/*.parquet')""").fetchone()

    def hkey(cx, cy):
        ix = min(N-1, max(0, int((cx-x0)/(x1-x0)*(N-1))))
        iy = min(N-1, max(0, int((cy-y0)/(y1-y0)*(N-1))))
        return xy2d(N, ix, iy)
    def zkey(cx, cy):
        ix = min(N-1, max(0, int((cx-x0)/(x1-x0)*(N-1))))
        iy = min(N-1, max(0, int((cy-y0)/(y1-y0)*(N-1))))
        return morton(ix, iy)
    c.create_function("hkey", hkey, ["DOUBLE", "DOUBLE"], "BIGINT")
    c.create_function("zkey", zkey, ["DOUBLE", "DOUBLE"], "BIGINT")

    ORDER = {
        "L0X": f"floor(((xmin+xmax)/2)/{CELL}), floor(((ymin+ymax)/2)/{CELL}), tmin",
        "L0Z": f"zkey({CEN}), tmin",
        "L0H": f"hkey({CEN}), tmin",
    }

    src_files = _list(L0_ROOT)
    for m, out in OUT.items():
        _st = time.perf_counter()
        _reset(out)
        for src in src_files:
            rel = src[len(L0_ROOT) + 1:]
            dst = f"{out}/{rel}"
            if not s3io.is_s3(dst):
                Path(dst).parent.mkdir(parents=True, exist_ok=True)
            c.execute(f"""
                COPY (SELECT {_COLS} FROM read_parquet('{src}') ORDER BY {ORDER[m]})
                TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE {RG})""")
        rows = c.execute(f"SELECT count(*) FROM read_parquet('{out}/**/*.parquet')").fetchone()[0]
        print(f"[{m}] {len(src_files)} day-files, {rows:,} rows, rg={RG}  total_s={time.perf_counter()-_st:.3f}")

if __name__ == "__main__":
    main()
