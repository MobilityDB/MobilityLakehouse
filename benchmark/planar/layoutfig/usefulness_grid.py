"""Draw panels (c) and (d) of the usefulness figure: the files of the adaptive tiling L3 as a grid,
one row per day and one column per region cell holding data, (c) every file of the run as the
catalog lists it, (d) the files the catalog admits for the query of panel (b) marked.

The style is 74_figures.py's (`#printed`, `#style`): the panel is 0.48 of the text width MDPI's
class prints at, and its text is 7 pt, so the figure reads at the size of the paper's other
figures. The constants are restated rather than imported because 74_figures.py runs its drawing at
import.

    usefulness_grid.py USE_FILES_CSV FILES_PNG ADMITTED_PNG

USE_FILES_CSV is the use_files.csv 04_usefulness_export.sql writes: day, cell and admission of
every file.
"""
import csv
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

TEXTWIDTH_IN = 506.17435 / 72.27
PT = 7.0
INK, MUTED = "#0b0b0b", "#52514e"
FILE, ADMITTED, DISCARDED = "#2166ac", "#e66101", "#d9d9d9"
W = 0.48 * TEXTWIDTH_IN
H = W * 0.75


def panel(files, ncells, days, admitted_only, out):
    """One grid: every file a rectangle at its (cell, day), coloured by what the panel shows.

    A grid figure beside `#heatmap_figure` of 74_figures.py, which draws its cells as an image; a
    file here is a patch so that its white edge separates it from its neighbours at print size.
    """
    fig, ax = plt.subplots(figsize=(W, H))
    for x, y, adm in files:
        color = (ADMITTED if adm else DISCARDED) if admitted_only else FILE
        ax.add_patch(Rectangle((x, y), 1, 1, facecolor=color, edgecolor="white", linewidth=0.15))
    ax.set_xlim(0, ncells)
    ax.set_ylim(len(days), 0)
    # The first day, one tick a week from the 7th, and the last day, labelled by the day of the month
    ticks = sorted({0, len(days) - 1} | set(range(6, len(days), 7)))
    ax.set_yticks([t + 0.5 for t in ticks])
    ax.set_yticklabels([str(int(days[t][-2:])) for t in ticks], fontsize=PT, color=MUTED)
    ax.set_xticks([])
    ax.set_xlabel(f"50 km cell holding data ({ncells} cells)", fontsize=PT, color=MUTED)
    ax.set_ylabel(f"day of {MONTHS[int(days[0][5:7]) - 1]} {days[0][:4]}", fontsize=PT,
                  color=MUTED)
    ax.tick_params(colors=MUTED, length=2)
    for s in ax.spines.values():
        s.set_visible(False)
    if admitted_only:
        n = sum(1 for f in files if f[2])
        text = f"{n} of {len(files):,} files admitted"
    else:
        text = f"{len(files):,} files, one per cell and day"
    ax.text(0.99, 1.02, text, transform=ax.transAxes, ha="right", va="bottom", fontsize=PT,
            color=INK)
    fig.tight_layout()
    fig.savefig(out, dpi=300, facecolor="white")
    plt.close(fig)
    print("wrote", out)


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def main(argv):
    """Read the files table and draw both panels, after `#catalog_files` of 74_figures.py."""
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print(f"usefulness_grid.py: {argv[1]} lists no file", file=sys.stderr)
        return 1
    cells = sorted({(int(r["cx"]), int(r["cy"])) for r in rows})
    col = {c: i for i, c in enumerate(cells)}
    days = sorted({r["fday"] for r in rows})
    row = {d: i for i, d in enumerate(days)}
    files = [(col[(int(r["cx"]), int(r["cy"]))], row[r["fday"]], r["admitted"] == "true")
             for r in rows]
    panel(files, len(cells), days, False, argv[2])
    panel(files, len(cells), days, True, argv[3])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
