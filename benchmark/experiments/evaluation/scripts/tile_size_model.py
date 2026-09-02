#!/usr/bin/env python3

import json
import os

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.environ.get("LAKEHOUSE_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".."))  # benchmark/
os.chdir(HERE)

L0 = "data/trips/L0/**/*.parquet"

# Region-cell sizes to sweep, metres. 50 km is the main benchmark's value.
SIZES = [12_500, 25_000, 50_000, 100_000, 200_000]

# The real query regions of Table tab:query-areas (UTM 32N metres).
REGIONS = {
    "rodby_port": (651135.0, 6058230.0, 651422.0, 6058548.0),
    "puttgarden_port": (644339.0, 6042108.0, 644896.0, 6042487.0),
    "goteborg_port": (666538.0, 6392057.0, 679171.0, 6403745.0),
    "belt": (640730.0, 6042487.0, 654100.0, 6058230.0),
}

con = duckdb.connect()
con.execute("PRAGMA threads=4")

# One heavy pass: pull each segment's blob size and bounds into a small table,
# so the sweep below never re-reads the trajectory column.
con.execute(
    f"""CREATE OR REPLACE TEMP TABLE bounds AS
        SELECT octet_length(traj) AS w, xmin, xmax, ymin, ymax
        FROM read_parquet('{L0}') WHERE xmin IS NOT NULL"""
)
N_SEG, W_SEG = con.execute("SELECT count(*), sum(w) FROM bounds").fetchone()
print(f"L0 segments: {N_SEG:,}  trajectory bytes: {W_SEG/1e9:.2f} GB")

rows = []
for S in SIZES:
    # Explode each segment into every region cell its bounding box overlaps.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE incid AS
        SELECT w, gx.cx AS cx, gy.cy AS cy
        FROM bounds,
             LATERAL UNNEST(generate_series(
                 CAST(floor(xmin/{S}) AS BIGINT),
                 CAST(floor(xmax/{S}) AS BIGINT))) AS gx(cx),
             LATERAL UNNEST(generate_series(
                 CAST(floor(ymin/{S}) AS BIGINT),
                 CAST(floor(ymax/{S}) AS BIGINT))) AS gy(cy);
        """
    )
    n_inc, w_inc, n_cells = con.execute(
        "SELECT count(*), sum(w), count(DISTINCT (cx, cy)) FROM incid"
    ).fetchone()
    rec = {
        "cell_km": S / 1000,
        "files_compact": int(n_cells),
        "repl_rows": round(n_inc / N_SEG, 3),
        "repl_bytes": round(w_inc / W_SEG, 3),
    }
    for name, (x0, y0, x1, y1) in REGIONS.items():
        touched_w, touched_cells = con.execute(
            f"""SELECT COALESCE(sum(w), 0), count(DISTINCT (cx, cy)) FROM incid
                WHERE cx BETWEEN CAST(floor({x0}/{S}) AS BIGINT)
                             AND CAST(floor({x1}/{S}) AS BIGINT)
                  AND cy BETWEEN CAST(floor({y0}/{S}) AS BIGINT)
                             AND CAST(floor({y1}/{S}) AS BIGINT)"""
        ).fetchone()
        rec[f"share_{name}_pct"] = round(100.0 * touched_w / w_inc, 3)
        rec[f"cells_{name}"] = int(touched_cells)
    rows.append(rec)
    print(rec)

# ---------- outputs ----------
import csv

with open("tile_size_model.csv", "w", newline="") as f:
    wcsv = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    wcsv.writeheader()
    wcsv.writerows(rows)
json.dump(
    {"l0_segments": N_SEG, "l0_traj_bytes": W_SEG, "sweep": rows},
    open("tile_size_model.json", "w"),
    indent=2,
)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
km = [r["cell_km"] for r in rows]
for name, label, c in [
    ("belt", "ferry belt (210 km$^2$)", "#08519c"),
    ("goteborg_port", "Göteborg port (148 km$^2$)", "#4292c6"),
    ("puttgarden_port", "Puttgarden port (0.2 km$^2$)", "#d6604d"),
    ("rodby_port", "Rødby port (0.1 km$^2$)", "#f4a582"),
]:
    ax1.plot(km, [r[f"share_{name}_pct"] for r in rows], "o-", label=label, color=c)
ax1.set_xscale("log")
ax1.set_yscale("log")
ax1.set_xticks(km, [f"{k:g}" for k in km])
ax1.set_xlabel("region cell size (km)")
ax1.set_ylabel("bytes in touched files (% of table)")
ax1.set_title("File-level candidate share vs tile size")
ax1.legend(fontsize=8)
ax1.spines[["top", "right"]].set_visible(False)

ax2.plot(km, [r["files_compact"] for r in rows], "s-", color="#08519c", label="files (compact)")
ax2.set_xscale("log")
ax2.set_xticks(km, [f"{k:g}" for k in km])
ax2.set_xlabel("region cell size (km)")
ax2.set_ylabel("non-empty cell files", color="#08519c")
ax2b = ax2.twinx()
ax2b.plot(km, [r["repl_bytes"] for r in rows], "^--", color="#d6604d",
          label="byte replication")
ax2b.set_ylabel("file-level byte replication ($\\times$)", color="#d6604d")
ax2.set_title("Files and replication vs tile size")
ax2.spines[["top"]].set_visible(False)
plt.tight_layout()
plt.savefig("figures/eval_tile_size.png", dpi=150)
print("wrote tile_size_model.csv / .json / figures/eval_tile_size.png")
