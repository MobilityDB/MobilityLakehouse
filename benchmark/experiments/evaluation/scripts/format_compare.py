# Table-format + architecture comparison on *data read*, not runtime
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import duckdb
from lakehouse.query.registry import MOBILITYDUCK_EXT  # noqa: E402
from lakehouse.store.catalog import NAMESPACE  # noqa: E402
from scripts.run_queries import measure_files, set_window, statements  # noqa: E402

SCRATCH = os.getenv(
    "DL_META",
    os.getenv("LAKEHOUSE_SCRATCH", tempfile.gettempdir()),
)
QDIR = Path("queries/iceberg")
# The thesis pruning figure (eval_pruning_bytes) averages these three windows and plots
# bytes_read as a PERCENTAGE of each layout's own table, so anything meant to sit next to
# it must be measured the same way. WINDOWS/TOTALS exist for exactly that.
WINDOWS = ["hour", "day", "week"]
WINDOW = "day"        # single-window default, used when WINDOWS is overridden to one entry
N_ITERS = 5           # matches the thesis harness (results/*.csv all use n_iters=5)

# table totals (bytes) for the percentage convention; DuckLake registers the *same*
# objects as Iceberg L3s, so it shares that denominator.
TOTALS = {
    "iceberg_L0": 3_554_384_337,
    "iceberg_L3s": 3_803_000_446,
    "ducklake_L3s": 3_803_000_446,
}
ORDER = ["clip_to_region", "harbour_entry", "both_ports", "position_interpolation",
         "collision", "encounter_zone", "nearest_approach", "fleet_summary",
         "bounding_box", "speed_profile"]

# view over `trips` per backend; the .sql files all read from `trips`
BACKENDS = {
    "iceberg_L0":   f"SELECT * FROM lake.{NAMESPACE}.L0",
    "iceberg_L3s":  f"SELECT * FROM lake.{NAMESPACE}.L3s",
    "ducklake_L3s": "SELECT * FROM dl.L3s",
}

def connect(*, dl: bool, ice: bool) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    exts = ["httpfs", "spatial"] + (["iceberg"] if ice else []) + (["ducklake"] if dl else [])
    for ext in exts:
        c.execute(f"INSTALL {ext}; LOAD {ext};")
    c.execute(f"LOAD '{MOBILITYDUCK_EXT}';")
    ep = os.getenv("ICEBERG_CATALOG_PROP__S3__ENDPOINT",
                   "http://localhost:9000").removeprefix("http://")
    c.execute(f"CREATE SECRET (TYPE S3, KEY_ID '{os.getenv('AWS_ACCESS_KEY_ID','admin')}', "
              f"SECRET '{os.getenv('AWS_SECRET_ACCESS_KEY','password')}', ENDPOINT '{ep}', "
              f"URL_STYLE 'path', USE_SSL false, REGION 'us-east-1');")
    if ice:
        rest = os.getenv("ICEBERG_REST_URI", "http://localhost:8181")
        c.execute(f"ATTACH '' AS lake (TYPE ICEBERG, ENDPOINT '{rest}', "
                  f"AUTHORIZATION_TYPE 'none');")
    if dl:
        c.execute(f"ATTACH 'ducklake:{SCRATCH}' AS dl "
                  f"(DATA_PATH 's3://warehouse/trips/layout_compact/L3s/');")
    return c

def cold_bytes(view_sql: str, stmts: list[str], window: str, *, dl: bool, ice: bool) -> int | None:
    cc = path = None
    try:
        cc = connect(dl=dl, ice=ice)
        cc.execute(f"CREATE OR REPLACE VIEW trips AS {view_sql}")
        set_window(cc, window)
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        cc.execute("PRAGMA enable_profiling='json'")
        cc.execute(f"PRAGMA profiling_output='{path}'")
        total = 0
        for s in stmts:
            cc.execute(s).fetchall()
            try:
                with open(path) as fh:
                    total += int(json.load(fh).get("total_bytes_read") or 0)
            except Exception:
                pass
        cc.execute("PRAGMA disable_profiling")
        return total or None
    except Exception as e:
        print(f"    cold_bytes failed: {type(e).__name__}: {e}")
        return None
    finally:
        if cc is not None:
            cc.close()
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass

def trimmed(xs: list[float]) -> float:
    return statistics.mean(sorted(xs)[1:-1]) if len(xs) >= 5 else statistics.median(xs)

def main() -> None:
    rows = []
    for backend, view_sql in BACKENDS.items():
        dl, ice = backend.startswith("ducklake"), backend.startswith("iceberg")
        qs = [(q, statements((QDIR / f"{q}.sql").read_text()))
              for q in ORDER if (QDIR / f"{q}.sql").exists()]
        total = TOTALS.get(backend)

        for window in WINDOWS:
            # --- warm pass: files opened + runtime (one connection) ---
            con = connect(dl=dl, ice=ice)
            con.execute(f"CREATE OR REPLACE VIEW trips AS {view_sql}")
            set_window(con, window)
            rtot = con.execute("SELECT count(*) FROM trips").fetchone()[0]
            print(f"\n=== {backend} · window={window}  rows={rtot:,} ===", flush=True)
            warm = {}
            for q, stmts in qs:
                nfiles = measure_files(con, stmts)     # also warms the scan
                ts, ans = [], None
                for _ in range(N_ITERS):
                    t = time.perf_counter()
                    for s in stmts[:-1]:
                        con.execute(s)
                    ans = con.execute(stmts[-1]).fetchall()
                    ts.append(time.perf_counter() - t)
                warm[q] = (nfiles, ans[0][0] if ans else None, round(1000 * trimmed(ts), 1))
            con.close()                               # release the DuckLake file handle

            # --- cold pass: bytes read (fresh connection per query, none held open) ---
            for q, stmts in qs:
                nbytes = cold_bytes(view_sql, stmts, window, dl=dl, ice=ice)
                nfiles, answer, ms = warm[q]
                pct = round(100 * nbytes / total, 3) if (nbytes and total) else None
                rows.append({"backend": backend, "selectivity": window, "query": q,
                             "answer": answer, "files_read": nfiles,
                             "bytes_read": nbytes, "bytes_total": total,
                             "bytes_pct": pct, "rows_total": rtot,
                             "n_iters": N_ITERS, "runtime_ms": ms})
                mb = f"{nbytes/1e6:.1f}" if nbytes else "?"
                print(f"  {q:24} ans={str(answer):>9}  files={nfiles}  "
                      f"bytes={mb} MB ({pct}%)  {ms} ms", flush=True)

    import pandas as pd
    df = pd.DataFrame(rows)
    df.to_csv("results/format_compare.csv", index=False)
    print("\nwrote results/format_compare.csv")
    print("\nbytes_pct (% of own table) by window, the eval_pruning_bytes convention:")
    print(df.pivot_table(index="selectivity", columns="backend",
                         values="bytes_pct", aggfunc="mean").round(3).to_string())
    print("\naveraged over all windows (the figure's headline number):")
    print(df.groupby("backend").bytes_pct.mean().round(3).to_string())

if __name__ == "__main__":
    main()
