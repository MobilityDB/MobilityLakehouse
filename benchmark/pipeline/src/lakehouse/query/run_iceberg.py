from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import pandas as pd

from lakehouse.store import s3io
from lakehouse.query.registry import MOBILITYDUCK_EXT, RESULTS, layouts
from lakehouse.store.catalog import NAMESPACE, catalog, table_name

QUERY_DIR = Path("queries/iceberg")

WINDOWS: dict[str, tuple[str, str, str, str, str, str]] = {
    "hour": ("2026-01-15 08:00:00", "2026-01-15 09:00:00",
             "2026-01-15 08:30:00", "2026-01-15", "2026-01-15", "1 hour"),
    "day":  ("2026-01-15 08:00:00", "2026-01-16 08:00:00",
             "2026-01-15 20:00:00", "2026-01-15", "2026-01-16", "1 day"),
    "week": ("2026-01-15 08:00:00", "2026-01-22 08:00:00",
             "2026-01-18 20:00:00", "2026-01-15", "2026-01-22", "1 week"),
    "month": ("2026-01-01 00:00:00", "2026-02-01 00:00:00",
              "2026-01-16 00:00:00", "2026-01-01", "2026-01-31", "1 month"),
}

def set_window(con, sel: str) -> None:
    t0, t1, tmid, d0, d1, _ = WINDOWS[sel]
    con.execute(f"SET VARIABLE t0   = TIMESTAMP '{t0}'")
    con.execute(f"SET VARIABLE t1   = TIMESTAMP '{t1}'")
    con.execute(f"SET VARIABLE tmid = TIMESTAMP '{tmid}'")
    con.execute(f"SET VARIABLE d0   = DATE '{d0}'")
    con.execute(f"SET VARIABLE d1   = DATE '{d1}'")

SPATIAL_CENTER = (647415.0, 6050358.5)
SPATIAL_SIDES_M = [300, 1000, 4000, 14000, 50000, 150000]
SPATIAL_WINDOW = "day"

def spatial_boxes() -> list[dict]:
    cx, cy = SPATIAL_CENTER
    out = []
    for side in SPATIAL_SIDES_M:
        h = side / 2.0
        out.append({"side_m": side, "km2": round(side * side / 1e6, 3),
                    "xlo": cx - h, "xhi": cx + h, "ylo": cy - h, "yhi": cy + h})
    return out

def spatial_query(box: dict) -> str:
    return (
        "SELECT count(DISTINCT mmsi) FROM trips "
        f"WHERE xmin <= {box['xhi']} AND xmax >= {box['xlo']} "
        f"AND ymin <= {box['yhi']} AND ymax >= {box['ylo']} "
        "AND tmin < getvariable('t1') AND tmax > getvariable('t0') "
        "AND dt BETWEEN getvariable('d0') AND getvariable('d1') "
        f"AND eIntersects(tgeompointFromEWKB(traj), "
        f"ST_MakeEnvelope({box['xlo']}, {box['ylo']}, {box['xhi']}, {box['yhi']}))"
    )

def connect() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    c.execute("SET TimeZone='UTC';")
    c.execute("INSTALL iceberg; LOAD iceberg;")
    c.execute("INSTALL httpfs; LOAD httpfs;")
    c.execute(f"LOAD '{MOBILITYDUCK_EXT}';")
    c.execute("INSTALL spatial; LOAD spatial;")

    s3io.attach_s3(c)
    rest = os.getenv("ICEBERG_REST_URI", "http://localhost:8181")
    c.execute(f"ATTACH '' AS lake (TYPE ICEBERG, ENDPOINT '{rest}', "
              f"AUTHORIZATION_TYPE 'none');")
    return c

def statements(text: str) -> list[str]:
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]

def files_read(plan: str) -> int | None:
    total, found = 0, False
    lines = plan.splitlines()
    for i, l in enumerate(lines):
        if "Total Files Read" in l:
            for cand in [l] + lines[i + 1:i + 4]:
                m = re.search(r"(\d[\d,]*)", cand)
                if m:
                    total += int(m.group(1).replace(",", ""))
                    found = True
                    break
    return total if found else None

def trimmed(xs: list[float]) -> float:
    if len(xs) >= 5:
        s = sorted(xs)[1:-1]
        return statistics.mean(s)
    return statistics.median(xs)

def measure_files(con, stmts: list[str]) -> int | None:
    opened = None
    for s in stmts:
        if "from trips" not in s.lower():
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

_SCAN_KW = ("PARQUET", "ICEBERG", "SCAN")

def _collect_scan_card(node, acc: list[int]) -> None:
    if isinstance(node, dict):
        name = str(node.get("operator_name") or node.get("name") or "").upper()
        if any(k in name for k in _SCAN_KW):
            card = node.get("operator_cardinality", node.get("cardinality"))
            if card is not None:
                try:
                    acc.append(int(card))
                except (TypeError, ValueError):
                    pass
        for v in node.values():
            _collect_scan_card(v, acc)
    elif isinstance(node, list):
        for x in node:
            _collect_scan_card(x, acc)

def _scan_rows_profile(con, sel: str) -> int | None:
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        con.execute("PRAGMA enable_profiling='json'")
        con.execute(f"PRAGMA profiling_output='{path}'")
        con.execute(sel).fetchall()
        con.execute("PRAGMA disable_profiling")
        with open(path) as fh:
            prof = json.load(fh)
        acc: list[int] = []
        _collect_scan_card(prof, acc)
        return sum(acc) if acc else None
    except Exception:
        try:
            con.execute("PRAGMA disable_profiling")
        except Exception:
            pass
        return None
    finally:
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass

def scan_rows(con, stmts: list[str]) -> int | None:
    total = None
    for s in stmts:
        if "from trips" not in s.lower():
            continue
        sel = s
        if s.lstrip().upper().startswith("CREATE"):
            m = re.search(r"\bAS\b\s+(SELECT|WITH)", s, re.IGNORECASE)
            if m:
                sel = s[m.start(1):]
        r = _scan_rows_profile(con, sel)
        if r is not None:
            total = (total or 0) + r
    return total

def bytes_read_cold(ls_key: str, sel: str, stmts: list[str]) -> int | None:
    cc = None
    path = None
    try:
        cc = connect()
        cc.execute(f"CREATE OR REPLACE VIEW trips AS "
                   f"SELECT * FROM lake.{NAMESPACE}.{ls_key}")
        set_window(cc, sel)
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
    except Exception:
        return None
    finally:
        if cc is not None:
            cc.close()
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass

def run_pipeline(con, stmts: list[str]):
    for s in stmts[:-1]:
        con.execute(s)
    return con.execute(stmts[-1]).fetchall()

def run_spatial(cat, names, iters: int, out: str) -> None:
    boxes = spatial_boxes()
    rows = []
    for ls in layouts(names=names):
        try:
            tasks = list(cat.load_table(table_name(ls)).scan().plan_files())
            ntot = len(tasks)
            btot = sum(t.file.file_size_in_bytes for t in tasks)
        except Exception as e:
            print(f"\n--- skip {ls.key}: {type(e).__name__} ({e}) ---", flush=True)
            continue
        con = rtot = None
        for attempt in range(4):
            try:
                con = connect()
                con.execute(f"CREATE OR REPLACE VIEW trips AS "
                            f"SELECT * FROM lake.{NAMESPACE}.{ls.key}")
                rtot = con.execute("SELECT count(*) FROM trips").fetchone()[0]
                break
            except Exception as e:
                print(f"  [{ls.key}] setup attempt {attempt+1} failed: {type(e).__name__}; "
                      f"retrying...", flush=True)
                time.sleep(2.0 * (attempt + 1))
        if rtot is None:
            print(f"--- skip {ls.key}: setup failed after retries ---", flush=True)
            continue
        set_window(con, SPATIAL_WINDOW)
        print(f"\n=== {ls.key} spatial sweep  window={SPATIAL_WINDOW}  files_total={ntot} "
              f"rows_total={rtot:,} ===", flush=True)
        for box in boxes:
            stmts = [spatial_query(box)]
            opened = measure_files(con, stmts)
            scanned = scan_rows(con, stmts)
            nbytes = bytes_read_cold(ls.key, SPATIAL_WINDOW, stmts)
            times, ans = [], None
            for _ in range(iters):
                t = time.perf_counter()
                ans = run_pipeline(con, stmts)
                times.append(time.perf_counter() - t)
            answer = ans[0][0] if ans else None
            rows.append({
                "system": "duckdb_iceberg_rest",
                "layout": ls.key, "gran": ls.gran,
                "spatial_km2": box["km2"], "side_m": box["side_m"],
                "selectivity": SPATIAL_WINDOW, "span": WINDOWS[SPATIAL_WINDOW][5],
                "files_total": ntot, "files_read": opened,
                "files_pct": round(100 * opened / ntot, 1) if opened else None,
                "rows_total": rtot, "rows_scanned": scanned,
                "rows_pct": round(100 * scanned / rtot, 3) if (scanned and rtot) else None,
                "bytes_total": btot, "bytes_read": nbytes,
                "bytes_pct": round(100 * nbytes / btot, 3) if (nbytes and btot) else None,
                "n_iters": iters,
                "runtime_trimmed_s": trimmed(times),
                "runtime_mean_s": statistics.mean(times),
                "runtime_std_s": statistics.stdev(times) if len(times) > 1 else 0.0,
                "answer": answer,
            })
            mb = f"{nbytes/1e6:.1f}MB" if nbytes else "?"
            print(f"  {box['km2']:>9.3f} km2  files {opened}/{ntot}  rows {scanned}  "
                  f"bytes {mb}  ans={answer}  {trimmed(times):.3f}s", flush=True)
        con.close()
    RESULTS.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\nwrote {out}  ({len(rows)} rows)")

def load_queries(names: set[str] | None) -> dict[str, str]:
    return {p.stem: p.read_text()
            for p in sorted(QUERY_DIR.glob("*.sql"))
            if p.stem != "setup" and (not names or p.stem in names)}

def run_mem(cat, names, qnames, sels, iters: int, out: str) -> None:
    try:
        import psutil
    except ImportError:
        sys.exit("--mem needs psutil:  pip install psutil")
    import threading
    queries = load_queries(qnames)
    rows = []
    for ls in layouts(names=names):
        try:
            ntot = len(list(cat.load_table(table_name(ls)).scan().plan_files()))
        except Exception as e:
            print(f"\n--- skip {ls.key}: {type(e).__name__} ({e}) ---", flush=True)
            continue
        print(f"\n=== {ls.key} memory spot-check ===", flush=True)
        for sel in sels:
            for q, raw in queries.items():
                stmts = statements(raw)
                con = connect()
                con.execute(f"CREATE OR REPLACE VIEW trips AS "
                            f"SELECT * FROM lake.{NAMESPACE}.{ls.key}")
                set_window(con, sel)
                opened = measure_files(con, stmts)
                proc = psutil.Process()
                base = proc.memory_info().rss
                peak = [base]
                stop = threading.Event()

                def sample():
                    while not stop.is_set():
                        try:
                            peak[0] = max(peak[0], proc.memory_info().rss)
                        except Exception:
                            pass
                        time.sleep(0.005)

                th = threading.Thread(target=sample, daemon=True)
                th.start()
                times, ans = [], None
                try:
                    for _ in range(iters):
                        t = time.perf_counter()
                        ans = run_pipeline(con, stmts)
                        times.append(time.perf_counter() - t)
                finally:
                    stop.set()
                    th.join(timeout=1)
                pk = max(peak[0], proc.memory_info().rss)
                answer = ans[0][0] if ans else None
                rows.append({
                    "layout": ls.key, "gran": ls.gran, "query": q, "selectivity": sel,
                    "files_total": ntot, "files_read": opened,
                    "peak_rss_mb": round(pk / 1e6, 1),
                    "base_rss_mb": round(base / 1e6, 1),
                    "mem_delta_mb": round((pk - base) / 1e6, 1),
                    "n_iters": iters,
                    "runtime_trimmed_s": trimmed(times),
                    "answer": answer,
                })
                print(f"  {q:<16} {sel:<5} peak={pk / 1e6:7.1f}MB  "
                      f"delta={(pk - base) / 1e6:6.1f}MB  {trimmed(times):.3f}s", flush=True)
                con.close()
    RESULTS.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"\nwrote {out}  ({len(rows)} rows)")

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--sels", default="hour,day,week")
    ap.add_argument("--layouts", default="", help="L0,L2s,... (default: all)")
    ap.add_argument("--queries", default="", help="collision,both_ports,... (default: all)")
    ap.add_argument("--out", default=str(RESULTS / "iceberg.csv"))
    ap.add_argument("--spatial", action="store_true",
                    help="run the spatial-selectivity sweep instead of the query benchmark")
    ap.add_argument("--spatial-out", default=str(RESULTS / "spatial.csv"))
    ap.add_argument("--cold", action="store_true",
                    help="cold-cache timing: a fresh DuckDB connection per iteration "
                         "(clears engine buffer/metadata caches; connection setup is untimed)")
    ap.add_argument("--mem", action="store_true",
                    help="memory spot-check: peak process RSS per query (needs psutil). "
                         "Scope it small with --queries / --layouts / --sels.")
    ap.add_argument("--mem-out", default=str(RESULTS / "mem.csv"))
    args = ap.parse_args()

    if os.getenv("ICEBERG_CATALOG_TYPE") != "rest":
        sys.exit("Set REST env first:  source deploy/iceberg_rest/env.rest")

    sels = [s.strip() for s in args.sels.split(",") if s.strip()]
    names = {s.strip() for s in args.layouts.split(",")} if args.layouts else None
    qnames = {s.strip() for s in args.queries.split(",")} if args.queries else None
    queries = load_queries(qnames)
    cat = catalog()

    if args.spatial:
        run_spatial(cat, names, args.iters, args.spatial_out)
        return

    if args.mem:
        run_mem(cat, names, qnames, sels, args.iters, args.mem_out)
        return

    rows = []
    for ls in layouts(names=names):
        try:
            tasks = list(cat.load_table(table_name(ls)).scan().plan_files())
            ntot = len(tasks)
            btot = sum(t.file.file_size_in_bytes for t in tasks)
        except Exception as e:
            print(f"\n--- skip {ls.key}: {type(e).__name__} ({e}) ---", flush=True)
            continue
        con = connect()
        con.execute(f"CREATE OR REPLACE VIEW trips AS "
                    f"SELECT * FROM lake.{NAMESPACE}.{ls.key}")
        rtot = con.execute("SELECT count(*) FROM trips").fetchone()[0]
        print(f"\n=== {ls.key} ({ls.desc})  files_total={ntot} rows_total={rtot:,} "
              f"bytes_total={btot/1e6:.0f}MB ===", flush=True)

        for sel in sels:
            set_window(con, sel)
            for q, raw in queries.items():
                stmts = statements(raw)
                opened = measure_files(con, stmts)
                scanned = scan_rows(con, stmts)
                nbytes = bytes_read_cold(ls.key, sel, stmts)
                times, ans = [], None
                for _ in range(args.iters):
                    if args.cold:
                        cc = connect()
                        cc.execute(f"CREATE OR REPLACE VIEW trips AS "
                                   f"SELECT * FROM lake.{NAMESPACE}.{ls.key}")
                        set_window(cc, sel)
                        t = time.perf_counter()
                        ans = run_pipeline(cc, stmts)
                        times.append(time.perf_counter() - t)
                        cc.close()
                    else:
                        t = time.perf_counter()
                        ans = run_pipeline(con, stmts)
                        times.append(time.perf_counter() - t)
                answer = ans[0][0] if ans else None
                rows_returned = len(ans) if ans is not None else None
                rows.append({
                    "system": "duckdb_iceberg_rest",
                    "cache": "cold" if args.cold else "warm",
                    "layout": ls.key, "gran": ls.gran, "query": q,
                    "selectivity": sel, "span": WINDOWS[sel][5],
                    "files_total": ntot, "files_read": opened,
                    "files_pct": round(100 * opened / ntot, 1) if opened else None,
                    "rows_total": rtot, "rows_scanned": scanned,
                    "rows_pct": round(100 * scanned / rtot, 3) if (scanned and rtot) else None,
                    "bytes_total": btot, "bytes_read": nbytes,
                    "bytes_pct": round(100 * nbytes / btot, 3) if (nbytes and btot) else None,
                    "rows_returned": rows_returned,
                    "n_iters": args.iters,
                    "runtime_trimmed_s": trimmed(times),
                    "runtime_mean_s": statistics.mean(times),
                    "runtime_std_s": statistics.stdev(times) if len(times) > 1 else 0.0,
                    "runtime_median_s": statistics.median(times),
                    "runtime_min_s": min(times),
                    "answer": answer,
                })
                mb = f"{nbytes/1e6:.1f}MB" if nbytes else "?"
                print(f"  {q:<22} {sel:<5} files {opened}/{ntot}  rows {scanned}  "
                      f"bytes {mb}  ans={answer}  {trimmed(times):.3f}s", flush=True)
        con.close()

    RESULTS.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(args.out, index=False)
    print(f"\nwrote {args.out}  ({len(rows)} rows)")

if __name__ == "__main__":
    main()
