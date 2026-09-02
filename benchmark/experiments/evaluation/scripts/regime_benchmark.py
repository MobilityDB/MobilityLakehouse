# The thesis benchmark queries, relocated across traffic regimes
from __future__ import annotations

import re
import statistics
import sys
import time
from pathlib import Path

import pandas as pd
from lakehouse.store.catalog import NAMESPACE  # noqa: E402
from scripts.run_queries import (bytes_read_cold, connect, measure_files,  # noqa: E402
                                 run_pipeline, set_window, statements)

QDIR = Path("queries/iceberg")
WINDOW = "day"
N_ITERS = 5

# the belt rectangle every relocatable query is written around
BELT = (640730.0, 6042487.0, 654100.0, 6058230.0)
HW, HH = (BELT[2] - BELT[0]) / 2, (BELT[3] - BELT[1]) / 2       # 6685.0, 7871.5
BELT_C = ((BELT[0] + BELT[2]) / 2, (BELT[1] + BELT[3]) / 2)

# same seven centres as the regime probe, so the two experiments line up
CENTERS = {
    # exact belt-box centre, so this row reproduces the thesis number as a control
    "belt":            (647415.0, 6050358.5, "corridor"),
    "goteborg":        (672854.0, 6397901.0, "corridor"),
    "oresund":         (725000.0, 6175000.0, "corridor"),
    "north_sea_west":  (249317.0, 6191062.0, "open_water"),
    "north_sea_south": (273380.0, 6044792.0, "open_water"),
    "skagerrak":       (517683.0, 6439882.0, "open_water"),
    "kattegat":        (635062.0, 6275026.0, "open_water"),
}

# 8 of 10; both_ports and harbour_entry name real port polygons and cannot move
QUERIES = ["clip_to_region", "fleet_summary", "bounding_box", "speed_profile",
           "position_interpolation", "nearest_approach", "collision", "encounter_zone"]

LAYOUTS = {"L0": 3_554_384_337, "L3s": 3_803_000_446}   # key -> table bytes

def relocate(sql: str, cx: float, cy: float) -> str:
    new = {f"{BELT[0]:.1f}": f"{cx - HW:.1f}", f"{BELT[1]:.1f}": f"{cy - HH:.1f}",
           f"{BELT[2]:.1f}": f"{cx + HW:.1f}", f"{BELT[3]:.1f}": f"{cy + HH:.1f}"}
    pat = re.compile("|".join(re.escape(k) for k in new))
    out = pat.sub(lambda m: new[m.group(0)], sql)
    if out == sql and (cx, cy) != BELT_C:
        raise RuntimeError("relocation substituted nothing: belt literals not found")
    return out

def main() -> None:
    rows = []
    for lay, total in LAYOUTS.items():
        con = connect()
        con.execute(f"CREATE OR REPLACE VIEW trips AS SELECT * FROM lake.{NAMESPACE}.{lay}")
        set_window(con, WINDOW)
        print(f"\n=== {lay} ===", flush=True)
        for region, (cx, cy, regime) in CENTERS.items():
            for q in QUERIES:
                sql = relocate((QDIR / f"{q}.sql").read_text(), cx, cy)
                stmts = statements(sql)
                nfiles = measure_files(con, stmts)      # doubles as warm-up
                nbytes = bytes_read_cold(lay, WINDOW, stmts)
                ts, ans = [], None
                for _ in range(N_ITERS):
                    t = time.perf_counter()
                    ans = run_pipeline(con, stmts)
                    ts.append(time.perf_counter() - t)
                answer = ans[0][0] if ans else None
                ms = round(1000 * (statistics.mean(sorted(ts)[1:-1]) if len(ts) >= 5
                                   else statistics.median(ts)), 1)
                rows.append({"layout": lay, "region": region, "regime": regime,
                             "query": q, "answer": answer, "files_read": nfiles,
                             "bytes_read": nbytes, "bytes_total": total,
                             "bytes_pct": round(100 * nbytes / total, 4) if nbytes else None,
                             "runtime_ms": ms, "n_iters": N_ITERS})
                print(f"  {region:16} {q:24} ans={str(answer):>9} "
                      f"files={nfiles} bytes={nbytes/1e6:7.1f} MB {ms:8.1f} ms", flush=True)
        con.close()

    df = pd.DataFrame(rows)
    df.to_csv("results/regime_benchmark.csv", index=False)
    print("\nwrote results/regime_benchmark.csv")

    p = df.pivot_table(index=["regime", "region"], columns="layout",
                       values="bytes_pct", aggfunc="mean")
    p["prune"] = (p.L0 / p.L3s).round(1)
    print("\nmean over the 8 relocatable queries, bytes read as % of table:")
    print(p.round(3).to_string())

if __name__ == "__main__":
    main()
