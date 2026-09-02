# Query answers on L0 by time window, as a grouped bar chart
import sys
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

CLI = Path(os.getenv("LAKEHOUSE_ROOT")
           or Path(__file__).resolve().parents[3])
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('.')
BOTH_PORTS_OVERRIDE = None

QORDER = ["both_ports", "harbour_entry", "clip_to_region", "fleet_summary",
          "bounding_box", "speed_profile", "position_interpolation",
          "nearest_approach", "collision", "encounter_zone"]
QNUM = {q: f"Q4.{i+1}" for i, q in enumerate(
    ["both_ports", "harbour_entry", "clip_to_region", "fleet_summary",
     "bounding_box", "speed_profile", "position_interpolation",
     "nearest_approach", "collision", "encounter_zone"])}
WINDOWS = ["hour", "day", "week"]
# three steps of one hue: the windows are ordered, so this is sequential, not
# categorical -- a three-colour categorical set would imply they are unrelated
SEQ = ["#9dc3ee", "#5a9bdc", "#1f5fa8"]
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#d8d7d2"

d = pd.read_csv(CLI / "results" / "iceberg_synced.csv")
d = d[d.layout == "L0"].copy()
d["ans"] = pd.to_numeric(d["answer"], errors="coerce")
p = d.pivot_table(index="query", columns="selectivity", values="ans",
                  aggfunc="first").reindex(QORDER)[WINDOWS]

if BOTH_PORTS_OVERRIDE:
    p.loc["both_ports"] = list(BOTH_PORTS_OVERRIDE)

fig, ax = plt.subplots(figsize=(11, 4.6))
x = np.arange(len(QORDER))
w = 0.26
for i, win in enumerate(WINDOWS):
    ax.bar(x + (i - 1) * w, p[win].values, w, label=win, color=SEQ[i],
           edgecolor="white", linewidth=0.8)

ax.set_yscale("log")
ax.set_xticks(x)
ax.set_xticklabels([QNUM[q] for q in QORDER], fontsize=9, color=INK)
ax.set_ylabel("answer  (log scale)", fontsize=9, color=MUTED)
ax.legend(title="window", frameon=False, fontsize=9, title_fontsize=9)
ax.grid(axis="y", color=GRID, lw=0.6, alpha=0.7)
ax.set_axisbelow(True)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
for s in ("left", "bottom"):
    ax.spines[s].set_color(GRID)
ax.tick_params(colors=MUTED)

# the answers span four orders of magnitude, so label the bars rather than make
# the reader map bar height back through a log axis
for i, win in enumerate(WINDOWS):
    for xi, v in zip(x + (i - 1) * w, p[win].values):
        if pd.isna(v) or v <= 0:
            continue
        ax.annotate(f"{v:g}", (xi, v), textcoords="offset points",
                    xytext=(0, 3), ha="center", fontsize=6.5, color=MUTED,
                    rotation=90)

fig.tight_layout()
out = OUT / "eval_query_answers.png"
fig.savefig(out, dpi=200, facecolor="white")
print(f"wrote {out}")
print(p.to_string())
