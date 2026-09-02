# The three speedup figures: daily family, sorted-compact family, per-query heatmap
from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

CLI = Path(os.getenv("LAKEHOUSE_ROOT")
           or Path(__file__).resolve().parents[3])
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else CLI / "figures"

DAILY = ["L0", "L0X", "L0Z", "L0H", "L1_daily", "L2_daily", "L3_daily", "L4_daily"]
COMPACT = ["L0", "L1s", "L2s", "L3s", "L4s"]
QORDER = ["both_ports", "harbour_entry", "clip_to_region", "fleet_summary",
          "bounding_box", "speed_profile", "position_interpolation",
          "nearest_approach", "collision", "encounter_zone"]
QNUM = {q: f"Q4.{i + 1}" for i, q in enumerate(QORDER)}
BLUE, ORANGE, INK, MUTED, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#d8d7d2"

def geo(s):
    return float(np.exp(np.log(s).mean()))

def load() -> pd.DataFrame:
    d = pd.read_csv(CLI / "results" / "iceberg_synced.csv")
    b = (d[d.layout == "L0"][["query", "selectivity", "runtime_trimmed_s"]]
         .rename(columns={"runtime_trimmed_s": "t0"}))
    m = d.merge(b, on=["query", "selectivity"])
    m["sp"] = m["t0"] / m["runtime_trimmed_s"]
    return m

def bar_family(m, order, out: Path):
    sp = m[m.layout.isin(order)].groupby("layout")["sp"].apply(geo).reindex(order).dropna()
    fig, ax = plt.subplots(figsize=(5.6, 4.0))
    cols = [ORANGE if v < 1 else BLUE for v in sp.values]
    ax.bar(range(len(sp)), sp.values, color=cols, edgecolor="white", linewidth=0.8)
    ax.axhline(1.0, color=MUTED, lw=1, ls=(0, (4, 3)))
    for i, v in enumerate(sp.values):
        ax.annotate(f"{v:.2f}×", (i, v), textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=8, color=INK)
    ax.set_xticks(range(len(sp)))
    ax.set_xticklabels([k.replace("_daily", "") for k in sp.index], fontsize=9, color=INK)
    ax.set_ylabel("speedup over $L0$  (geometric mean)", fontsize=9, color=MUTED)
    ax.set_ylim(0, max(sp.values) * 1.18)
    ax.grid(axis="y", color=GRID, lw=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=MUTED)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor="white")
    print(f"wrote {out}")
    return sp

def heatmap(m, out: Path):
    piv = (m.groupby(["query", "layout"])["sp"].apply(geo).unstack()
           .reindex(index=QORDER)[[c for c in DAILY[1:] + COMPACT[1:] if c in m.layout.unique()]])
    fig, ax = plt.subplots(figsize=(9.5, 4.6))
    # speedup is a ratio around 1, so a diverging map centred on 1 is the right
    # encoding: warm = slower than the baseline, cool = faster.
    v = np.log2(piv.values.astype(float))
    lim = np.nanmax(np.abs(v))
    im = ax.imshow(v, aspect="auto", cmap="RdBu", vmin=-lim, vmax=lim)
    ax.set_xticks(range(piv.shape[1]))
    ax.set_xticklabels([c.replace("_daily", "") for c in piv.columns],
                       rotation=45, ha="right", fontsize=8, color=INK)
    ax.set_yticks(range(piv.shape[0]))
    ax.set_yticklabels([QNUM[q] for q in piv.index], fontsize=8, color=INK)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            val = piv.values[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=6.5,
                        color="white" if abs(v[i, j]) > lim * 0.55 else INK)
    cb = fig.colorbar(im, ax=ax)
    cb.set_label("speedup over $L0$ ($\\log_2$)", fontsize=8, color=MUTED)
    cb.ax.tick_params(colors=MUTED, labelsize=7)
    ax.tick_params(colors=MUTED, length=0)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor="white")
    print(f"wrote {out}")

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    m = load()
    d = bar_family(m, DAILY, OUT / "eval_speedup_daily.png")
    c = bar_family(m, COMPACT, OUT / "eval_speedup_compact.png")
    heatmap(m, OUT / "eval_speedup_heatmap.png")
    print("\ndaily:"); print(d.round(2).to_string())
    print("compact:"); print(c.round(2).to_string())

if __name__ == "__main__":
    main()
