# Regenerate the seven appendix tables from the post-sync benchmark
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

BENCH = Path(os.getenv("LAKEHOUSE_ROOT")
             or Path(__file__).resolve().parents[3])
SRC = BENCH / "results" / "iceberg_synced.csv"
OUT = BENCH / "results" / "appendix_tables.csv"

COLS = ["L0", "L0X", "L0Z", "L0H", "L1_daily", "L2_daily", "L3_daily",
        "L4_daily", "L1s", "L2s", "L3s", "L4s"]
QROWS = ["both_ports", "harbour_entry", "clip_to_region", "fleet_summary",
         "bounding_box", "speed_profile", "position_interpolation",
         "nearest_approach", "collision", "encounter_zone"]

def load() -> tuple[pd.DataFrame, list[str]]:
    d = pd.read_csv(SRC)
    # the L4 family was measured at one iteration, the rest at five
    single = sorted(d[d.n_iters == 1].layout.unique())
    return d, single

def table(d: pd.DataFrame, value: str, sel: str, nd: int) -> pd.DataFrame:
    p = d[d.selectivity == sel].pivot_table(index="query", columns="layout",
                                            values=value, aggfunc="mean")
    p = p.reindex(index=[q for q in QROWS if q in p.index],
                  columns=[c for c in COLS if c in p.columns])
    return p.round(nd)

def main() -> None:
    d, missing = load()
    d["ansnum"] = pd.to_numeric(d["answer"], errors="coerce")
    if missing:
        print(f"measured at a single iteration: {missing}")

    blocks = {}
    for sel in ("hour", "day", "week"):
        blocks[f"bytes-{sel}"] = table(d, "bytes_pct", sel, 2)
        blocks[f"rt-{sel}"] = table(d, "runtime_trimmed_s", sel, 3)
    blocks["ans-week"] = table(d, "ansnum", "week", 2)

    # one long frame, so the seven tables live in a single tidy CSV
    out = pd.concat(
        [t.reset_index().assign(table=k) for k, t in blocks.items()],
        ignore_index=True,
    )
    out = out[["table", "query"] + [c for c in COLS if c in out.columns]]
    out.to_csv(OUT, index=False)
    print(f"wrote {OUT}  ({len(blocks)} tables, {len(out)} rows)")

    for k, t in blocks.items():
        print(f"\n== {k}\n{t.to_string()}")

if __name__ == "__main__":
    main()
