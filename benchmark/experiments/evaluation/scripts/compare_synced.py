# Compare the clock-synced rebuild against the saved pre-sync results
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import pandas as pd
from lakehouse.query.registry import RESULTS  # noqa: E402

# Count-type queries: L0's answer is the exact maximum, so answer/answer(L0) is a
# recall. Distance/area aggregates are excluded -- a ratio is not recall there.
COUNT_QUERIES = ["both_ports", "clip_to_region", "collision",
                 "encounter_zone", "fleet_summary", "harbour_entry"]
FOCUS = ["L0", "L2_daily", "L3_daily", "L2s", "L3s"]

def _scalar(v):
    if pd.isna(v):
        return None
    s = str(v).strip()
    if s.startswith("("):
        try:
            return float(ast.literal_eval(s)[0])
        except Exception:
            return None
    try:
        return float(s)
    except Exception:
        return None

def load(path: Path, tag: str) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"missing {path}")
    d = pd.read_csv(path)
    d = d[d.layout.isin(FOCUS)].copy()
    d["ans"] = d["answer"].map(_scalar)
    base = (d[d.layout == "L0"][["query", "selectivity", "ans"]]
            .rename(columns={"ans": "ans_L0"}))
    d = d.merge(base, on=["query", "selectivity"], how="left")
    d["recall_pct"] = (100 * d["ans"] / d["ans_L0"]).round(1)
    d["src"] = tag
    return d

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default=str(RESULTS / "iceberg.csv"))
    ap.add_argument("--new", default=str(RESULTS / "iceberg_synced.csv"))
    ap.add_argument("--out", default=str(RESULTS / "tab_synced_vs_presync.csv"))
    args = ap.parse_args()

    old = load(Path(args.old), "pre-sync")
    new = load(Path(args.new), "synced")
    both = pd.concat([old, new], ignore_index=True)

    print("=== recall vs L0 (%), count queries: pre-sync -> synced ===")
    piv = both[both["query"].isin(COUNT_QUERIES) & (both.layout != "L0")].pivot_table(
        index=["query", "selectivity"], columns=["layout", "src"],
        values="recall_pct", aggfunc="mean")
    print(piv.to_string())

    print("\n=== mean recall per layout (%) ===")
    mr = (both[both["query"].isin(COUNT_QUERIES) & (both.layout != "L0")]
          .groupby(["layout", "src"])["recall_pct"].mean().round(2).unstack())
    cols = [c for c in ("pre-sync", "synced") if c in mr.columns]
    mr = mr[cols]
    if len(cols) == 2:
        mr["delta"] = (mr["synced"] - mr["pre-sync"]).round(2)
    print(mr.to_string())

    for col, label in (("bytes_pct", "bytes read (% of table)"),
                       ("files_pct", "files read (%)"),
                       ("runtime_trimmed_s", "runtime (trimmed s)")):
        if col not in both.columns:
            continue
        print(f"\n=== {label}: pre-sync -> synced (mean over queries) ===")
        t = both.groupby(["layout", "src"])[col].mean().unstack()
        t = t[[c for c in ("pre-sync", "synced") if c in t.columns]]
        print(t.round(3).to_string())

    both.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}")

if __name__ == "__main__":
    main()
