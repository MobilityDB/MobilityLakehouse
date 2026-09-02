# Regenerate the two recall figures for §5 from the clock-synced benchmark
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
SRC = CLI / "results" / "iceberg_synced.csv"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else CLI / "figures"

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#d8d7d2"

ORDER = ["L0", "L0X", "L0Z", "L0H", "L1_daily", "L1s",
         "L2_daily", "L2s", "L3_daily", "L3s", "L4_daily", "L4s"]
LABEL = {k: k.replace("_daily", "") for k in ORDER}

# Recall is answer/answer(L0) and is only defined where the answer is a COUNT of
# entities that L0 maximises. fleet_summary returns total_km, bounding_box an
# area, nearest_approach and speed_profile a distance and a speed; a ratio of
# those is not a recall. position_interpolation does return a count and belongs.
COUNT_QUERIES = ["both_ports", "clip_to_region", "collision",
                 "encounter_zone", "harbour_entry", "position_interpolation"]

def load() -> tuple[pd.Series, pd.Series, pd.Series]:
    d = pd.read_csv(SRC)
    d["ans"] = pd.to_numeric(d["answer"], errors="coerce")

    a = d[d["query"].isin(COUNT_QUERIES)].copy()
    base = (a[a.layout == "L0"][["query", "selectivity", "ans"]]
            .rename(columns={"ans": "a0"}))
    a = a.merge(base, on=["query", "selectivity"])
    recall = (100 * a["ans"] / a["a0"]).groupby(a.layout).mean().reindex(ORDER)

    f = d[d["query"] == "fleet_summary"].copy()
    fb = f[f.layout == "L0"][["selectivity", "ans"]].rename(columns={"ans": "f0"})
    f = f.merge(fb, on="selectivity")
    length = (100 * f["ans"] / f["f0"]).groupby(f.layout).mean().reindex(ORDER)

    byt = d.groupby("layout")["bytes_pct"].mean().reindex(ORDER)
    return recall, length, byt

def dot_panel(ax, vals, xlim, xlabel, tol):
    y = range(len(ORDER))
    ax.hlines(list(y), xlim[0], vals.values, color=GRID, lw=1, zorder=1)
    colors = [ORANGE if v < 100 - tol else BLUE for v in vals.values]
    ax.scatter(vals.values, list(y), s=46, color=colors, zorder=3)
    for i, v in enumerate(vals.values):
        if v < 100 - tol:                      # label only what deviates
            ax.annotate(f"{v:.2f}%", (v, i), textcoords="offset points",
                        xytext=(-8, 0), ha="right", va="center",
                        fontsize=8, color=ORANGE, fontweight="bold")
    ax.axvline(100, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=2)
    ax.set_yticks(list(y))
    ax.set_yticklabels([LABEL[k] for k in ORDER], fontsize=9, color=INK)
    ax.invert_yaxis()
    ax.set_xlim(*xlim)
    ax.set_xlabel(xlabel, fontsize=9, color=MUTED)
    ax.grid(axis="x", color=GRID, lw=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)

def fig_recall(recall, length, out: Path):
    # Recall only. The path-length panel was dropped: it mixed a query-region
    # aggregate into a figure read alongside a table of query results, and the
    # chapter now reports geometric fidelity from the published fleet_summary
    # answer instead of as a separate whole-layout statistic.
    fig, a1 = plt.subplots(1, 1, figsize=(6.4, 4.2))
    dot_panel(a1, recall, (99.5, 100.12), "% of $L0$'s count", 0.01)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor="white")
    print(f"wrote {out}")

def fig_tradeoff(recall, byt, out: Path):
    fig, ax = plt.subplots(figsize=(8.4, 4.2))
    ax.set_xscale("log")
    colors = [ORANGE if v < 99.99 else BLUE for v in recall.values]
    ax.scatter(byt.values, recall.values, s=52, color=colors, zorder=3)

    # L0/L0X/L0Z/L0H/L1 sit within a couple of percentage points of each other at
    # exactly 100 %, so labelling each one individually overprints them into an
    # unreadable smear. They make the same point, so they get one shared label.
    CLUSTER = ["L0", "L0X", "L0Z", "L0H", "L1_daily"]
    OFF = {"L2s": (0, 8, "center"), "L2_daily": (0, 8, "center"),
           "L3s": (0, -15, "center"), "L3_daily": (0, -15, "center"),
           "L4s": (0, 8, "center"), "L1s": (-4, 8, "right"),
           "L4_daily": (0, -15, "center")}   # below: the cluster label sits above
    for k in ORDER:
        if k in CLUSTER:
            continue
        dx, dy, ha = OFF.get(k, (7, 6, "left"))
        ax.annotate(LABEL[k], (byt[k], recall[k]), textcoords="offset points",
                    xytext=(dx, dy), ha=ha, fontsize=8, color=INK)
    cx = (byt[CLUSTER].min() * byt[CLUSTER].max()) ** 0.5
    ax.annotate("L0, L0X, L0Z, L0H, L1", (cx, 100.0), textcoords="offset points",
                xytext=(0, 10), ha="center", fontsize=8, color=INK)

    ax.axhline(100, color=MUTED, lw=1, ls=(0, (4, 3)), zorder=2)
    ax.set_ylim(99.93, 100.045)
    # Without this matplotlib factors out a "+1e2" offset and the axis reads
    # 0.00 / -0.02 instead of 100.00 / 99.98 -- actively misleading on a recall axis.
    ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    ax.set_xlabel("% of stored bytes read   (log scale; left = more pruning)",
                  fontsize=9, color=MUTED)
    ax.set_ylabel("recall vs $L0$  (%)", fontsize=9, color=MUTED)
    ax.grid(color=GRID, lw=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=MUTED)
    fig.tight_layout()
    fig.savefig(out, dpi=200, facecolor="white")
    print(f"wrote {out}")

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    recall, length, byt = load()
    print(pd.DataFrame({"recall": recall.round(2), "length": length.round(2),
                        "bytes_pct": byt.round(3)}).to_string())
    fig_recall(recall, length, OUT / "eval_recall.png")
    fig_tradeoff(recall, byt, OUT / "eval_recall_pruning.png")

if __name__ == "__main__":
    main()
