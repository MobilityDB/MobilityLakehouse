# Redraw ds_reduction.png (raw messages vs cleaned segments)
from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

CLI = Path(os.getenv("LAKEHOUSE_ROOT")
           or Path(__file__).resolve().parents[3])
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else CLI / "figures"
RAW_TOTAL = 485_313_080          # summed from logs/rebuild_ingest_l0.log

con = duckdb.connect()
con.execute("SET enable_progress_bar=false")
seg = con.execute(
    f"SELECT count(*) FROM read_parquet('{(CLI / 'data/L0/L0/**/*.parquet').as_posix()}')"
).fetchone()[0]

plt.figure(figsize=(5, 4))
vals = [RAW_TOTAL, seg]
plt.bar(["raw AIS\nmessages", "cleaned\nsegments"], vals, color=["#bdbdbd", "#08519c"])
plt.yscale("log")
for i, v in enumerate(vals):
    plt.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=9)
plt.ylabel("count (log scale)")
plt.title(f"Raw to segments ({RAW_TOTAL / max(seg, 1):.0f}x reduction)")
plt.tight_layout()
OUT.mkdir(parents=True, exist_ok=True)
plt.savefig(OUT / "ds_reduction.png", dpi=150)
print(f"wrote {OUT / 'ds_reduction.png'}")
print(f"  raw={RAW_TOTAL:,}  segments={seg:,}  reduction={RAW_TOTAL / seg:,.0f}x")
