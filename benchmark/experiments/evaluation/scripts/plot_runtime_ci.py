# Runtime-stability figure with the L4 family filled in from the pre-sync run
from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

CLI = Path(os.getenv("LAKEHOUSE_ROOT")
           or Path(__file__).resolve().parents[3])
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else CLI / "figures"

ORDER = ["L0", "L0X", "L0Z", "L0H", "L1_daily", "L2_daily", "L3_daily",
         "L4_daily", "L1s", "L2s", "L3s", "L4s"]
BORROWED = {"L4_daily", "L4s"}
PLUM, INK, MUTED, GRID = "#b279a2", "#0b0b0b", "#52514e", "#d8d7d2"

_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571,
        6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262}

def tcrit(n):
    return _T95.get(int(n) - 1, 1.96)

def ci_pct(d: pd.DataFrame) -> pd.Series:
    n = d["n_iters"].clip(lower=2)
    base = d["runtime_mean_s"].replace(0, float("nan"))
    v = 100 * n.map(tcrit) * d["runtime_std_s"] / (n.pow(0.5) * base)
    return v.groupby(d["layout"]).mean()

synced = ci_pct(pd.read_csv(CLI / "results" / "iceberg_synced.csv"))
presync = ci_pct(pd.read_csv(CLI / "results" / "iceberg.csv"))
ci = pd.Series({k: (presync.get(k) if k in BORROWED else synced.get(k))
                for k in ORDER}).dropna()

fig, ax = plt.subplots(figsize=(10, 4))
bars = ax.bar(range(len(ci)), ci.values, color=PLUM, edgecolor="white", linewidth=0.8)
ax.set_xticks(range(len(ci)))
ax.set_xticklabels([k.replace("_daily", "") for k in ci.index], fontsize=9, color=INK)
ax.set_ylabel("95% CI half-width (% of mean)", fontsize=9, color=MUTED)
ax.set_xlabel("layout", fontsize=9, color=MUTED)
ax.grid(axis="y", color=GRID, lw=0.6, alpha=0.7)
ax.set_axisbelow(True)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
for s in ("left", "bottom"):
    ax.spines[s].set_color(GRID)
ax.tick_params(colors=MUTED)
fig.tight_layout()
OUT.mkdir(parents=True, exist_ok=True)
fig.savefig(OUT / "eval_runtime_ci.png", dpi=200, facecolor="white")
print(f"wrote {OUT / 'eval_runtime_ci.png'}")
print(ci.round(1).to_string())
