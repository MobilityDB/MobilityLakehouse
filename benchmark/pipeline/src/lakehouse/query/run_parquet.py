from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

from lakehouse.store import s3io
from lakehouse.query.registry import MOBILITYDUCK_EXT, RESULTS, TRIPS_DEST, layouts
from lakehouse.query.run_iceberg import WINDOWS, files_read, run_pipeline, set_window, statements, trimmed

LAKEHOUSE = TRIPS_DEST.rstrip("/")
QUERY_DIR = Path("queries/parquet")

def connect() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    c.execute("SET TimeZone='UTC';")
    c.execute("INSTALL httpfs; LOAD httpfs;")
    c.execute(f"LOAD '{MOBILITYDUCK_EXT}';")
    c.execute("INSTALL spatial; LOAD spatial;")

    s3io.attach_s3(c)
    return c

def measure_files(con, stmts: list[str]) -> int | None:
    opened = None
    for s in stmts:
        if "read_parquet" not in s.lower():
            continue
        sel = s
        if s.lstrip().upper().startswith("CREATE"):
            m = re.search(r"\bAS\b\s+(SELECT|WITH)", s, re.IGNORECASE)
            if m:
                sel = s[m.start(1):]
        plan = con.execute(f"EXPLAIN ANALYZE {sel}").fetchall()[0][1]
        fr = files_read(plan)
        if fr is not None:
            opened = (opened or 0) + fr
    return opened

def load_queries(names: set[str] | None) -> dict[str, str]:
    return {p.stem: p.read_text()
            for p in sorted(QUERY_DIR.glob("*.sql"))
            if p.stem != "setup" and (not names or p.stem in names)}

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--sels", default="hour,day,week")
    ap.add_argument("--layouts", default="", help="L0,L2s,... (default: all)")
    ap.add_argument("--queries", default="", help="collision,both_ports,... (default: all)")
    ap.add_argument("--out", default=str(RESULTS / "parquet.csv"))
    args = ap.parse_args()

    sels = [s.strip() for s in args.sels.split(",") if s.strip()]
    names = {s.strip() for s in args.layouts.split(",")} if args.layouts else None
    qnames = {s.strip() for s in args.queries.split(",")} if args.queries else None
    queries = load_queries(qnames)

    con = connect()
    sub = {ls.key: ls.subdir for ls in layouts()}
    rows = []
    for ls in layouts(names=names):
        glob = f"{LAKEHOUSE}/{sub[ls.key]}/**/*.parquet"
        ntot = con.execute(f"SELECT count(*) FROM glob('{glob}')").fetchone()[0]
        if not ntot:
            print(f"\n--- skip {ls.key}: no parquet at {glob} ---", flush=True)
            continue
        con.execute(f"SET VARIABLE trips_glob = '{glob}'")
        print(f"\n=== {ls.key} ({ls.desc})  files_total={ntot} ===", flush=True)

        for sel in sels:
            set_window(con, sel)
            for q, raw in queries.items():
                stmts = statements(raw)
                opened = measure_files(con, stmts)
                times, ans = [], None
                for _ in range(args.iters):
                    t = time.perf_counter()
                    ans = run_pipeline(con, stmts)
                    times.append(time.perf_counter() - t)
                rows.append({
                    "system": "duckdb_parquet",
                    "layout": ls.key, "gran": ls.gran, "query": q,
                    "selectivity": sel, "span": WINDOWS[sel][5],
                    "files_total": ntot, "files_read": opened,
                    "files_pct": round(100 * opened / ntot, 1) if opened else None,
                    "n_iters": args.iters,
                    "runtime_trimmed_s": trimmed(times),
                    "runtime_median_s": statistics.median(times),
                    "runtime_min_s": min(times),
                    "answer": ans[0] if ans else None,
                })
                print(f"  {q:<22} {sel:<5} files {opened}/{ntot}  "
                      f"ans={ans[0] if ans else None}  {trimmed(times):.3f}s", flush=True)
    con.close()

    RESULTS.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out, index=False)
    print(f"\nwrote {args.out}  ({len(rows)} rows)")

if __name__ == "__main__":
    main()
