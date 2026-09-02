# Recall, path length kept and bytes read per layout, from the clock-synced benchmark
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

BENCH = Path(os.getenv("LAKEHOUSE_ROOT")
             or Path(__file__).resolve().parents[3])
SRC = BENCH / "results" / "iceberg_synced.csv"
OUT = BENCH / "results" / "tab_eval_recall.csv"

ROWS = [
    ("L0", "L0", "baseline, whole segments"),
    ("L0X", "L0X", "in-file lexicographic sort"),
    ("L0Z", "L0Z", "in-file Z-order sort"),
    ("L0H", "L0H", "in-file Hilbert sort"),
    ("L1_daily", "L1", "hash(mmsi), daily"),
    ("L1s", "L1s", "hash(mmsi), compact"),
    ("L2_daily", "L2", "regular tiles, daily"),
    ("L2s", "L2s", "regular tiles, compact"),
    ("L3_daily", "L3", "adaptive tiles, daily"),
    ("L3s", "L3s", "adaptive tiles, compact"),
    ("L4_daily", "L4", "time bins, daily"),
    ("L4s", "L4s", "time bins, compact"),
]
SPLITS = {"L2_daily", "L2s", "L3_daily", "L3s", "L4_daily", "L4s"}

# Recall is answer/answer(L0), defined only where the answer counts entities that
# L0 maximises. fleet_summary returns total km, bounding_box an area,
# nearest_approach a distance, speed_profile a speed: ratios of those are not
# recall. position_interpolation returns count(DISTINCT mmsi) and belongs here.
COUNT_QUERIES = ["both_ports", "clip_to_region", "collision",
                 "encounter_zone", "harbour_entry", "position_interpolation"]

def main() -> None:
    d = pd.read_csv(SRC)
    d["ans"] = pd.to_numeric(d["answer"], errors="coerce")

    a = d[d["query"].isin(COUNT_QUERIES)].copy()
    base = (a[a.layout == "L0"][["query", "selectivity", "ans"]]
            .rename(columns={"ans": "a0"}))
    a = a.merge(base, on=["query", "selectivity"])
    a["r"] = 100 * a["ans"] / a["a0"]
    recall = a.groupby("layout")["r"].mean()
    worst = a.groupby("layout")["r"].min()

    f = d[d["query"] == "fleet_summary"].copy()
    fb = f[f.layout == "L0"][["selectivity", "ans"]].rename(columns={"ans": "f0"})
    f = f.merge(fb, on="selectivity")
    length = (100 * f["ans"] / f["f0"]).groupby(f.layout).mean()

    byt = d.groupby("layout")["bytes_pct"].mean()

    rows = []
    for key, name, desc in ROWS:
        if key not in recall.index:
            continue
        rows.append({
            "layout": name,
            "arrangement": desc,
            "splits_paths": key in SPLITS,
            "recall_pct": round(recall[key], 2),
            "worst_cell_pct": round(worst[key], 2),
            "length_kept_pct": round(length[key], 2),
            "bytes_read_pct": round(byt[key], 2),
        })
    t = pd.DataFrame(rows)
    t.to_csv(OUT, index=False)
    print(t.to_string(index=False))
    print(f"\nwrote {OUT}")

if __name__ == "__main__":
    main()
