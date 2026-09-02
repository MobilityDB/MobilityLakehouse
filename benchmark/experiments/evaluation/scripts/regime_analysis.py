#!/usr/bin/env python3
# Traffic-regime sensitivity of file-level pruning (corridor vs open water)
import csv
import json
import os

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ----------------------------------------------------------------------------- config
# Run from benchmark/ by default (mirrors tile_size_model.py).
if os.path.isdir("scripts") and os.path.basename(os.getcwd()) != "cli":
    pass  # already at a sensible root
HERE = os.environ.get("REGIME_CLI_ROOT")
if HERE:
    os.chdir(HERE)

L0_GLOB = os.environ.get("REGIME_L0_GLOB", "data/trips/L0/**/*.parquet")

TILE_M = 50_000          # spatial tiling of the L2/L3 layout (main benchmark)
QUERY_BOX_M = 10_000     # side of the fixed "small local query" box for the scatter (A)
DENSITY_QUANTILE_LOW = 0.20   # <=20th pct non-empty tiles are "open water"
DENSITY_QUANTILE_HIGH = 0.80  # >=80th pct non-empty tiles are "corridor"
N_OPEN_REGIONS = 4       # named open-water regions to auto-pick for table (B)

# The thesis' corridor query regions (EPSG:32632 metres), from lakehouse.query.registry.
CORRIDOR_REGIONS = {
    "rodby (port)":      (651135.0, 6058230.0, 651422.0, 6058548.0),
    "puttgarden (port)": (644339.0, 6042108.0, 644896.0, 6042487.0),
    "goteborg (port)":   (666538.0, 6392057.0, 679171.0, 6403745.0),
    "belt (corridor)":   (640730.0, 6042487.0, 654100.0, 6058230.0),
}

# OPTIONAL: fill in real open-water query regions you know are open sea
# (central Kattegat, Skagerrak, North Sea approaches), as
#   "name": (xmin, ymin, xmax, ymax)  in EPSG:32632 metres.
# If left empty, the script auto-selects low-density regions from the data.
OPEN_WATER_REGIONS: dict[str, tuple] = {
    # belt-sized boxes (13.4 x 15.7 km) centred on open-water points read off the
    # traffic heatmap (Fig. ds_spatial_heatmap). north_sea_* are the low-density
    # dispersed regime; skagerrak/kattegat are open sea but still on trafficked
    # routes (a useful middle regime). Adjust to taste.
    "north_sea_west":  (242631.9, 6183191.2, 256001.9, 6198934.2),   # ~5.0E 55.8N
    "north_sea_south": (266695.5, 6036920.7, 280065.5, 6052663.7),   # ~5.5E 54.5N
    "skagerrak_open":  (510998.3, 6432011.4, 524368.3, 6447754.4),   # ~9.3E 58.1N
    "kattegat_open":   (628377.8, 6267154.9, 641747.8, 6282897.9),   # ~11.2E 56.6N
}

con = duckdb.connect()
con.execute("PRAGMA threads=4")

# --------------------------------------------------------------------------- load bounds
# One heavy pass: pull each segment's blob size, bounds and centroid.
con.execute(
    f"""CREATE OR REPLACE TEMP TABLE bounds AS
        SELECT octet_length(traj) AS w,
               xmin, xmax, ymin, ymax,
               (xmin + xmax) / 2.0 AS cx0,
               (ymin + ymax) / 2.0 AS cy0
        FROM read_parquet('{L0_GLOB}') WHERE xmin IS NOT NULL"""
)
N_SEG, W_SEG = con.execute("SELECT count(*), sum(w) FROM bounds").fetchone()
print(f"L0 segments: {N_SEG:,}   trajectory bytes: {W_SEG/1e9:.3f} GB")

# Incidence of each segment into every TILE_M cell its bounding box overlaps
# (this is what the L2/L3 tiling physically stores, with replication).
con.execute(
    f"""CREATE OR REPLACE TEMP TABLE incid AS
        SELECT w,
               gx.cx AS cx, gy.cy AS cy
        FROM bounds,
             LATERAL UNNEST(generate_series(
                 CAST(floor(xmin/{TILE_M}) AS BIGINT),
                 CAST(floor(xmax/{TILE_M}) AS BIGINT))) AS gx(cx),
             LATERAL UNNEST(generate_series(
                 CAST(floor(ymin/{TILE_M}) AS BIGINT),
                 CAST(floor(ymax/{TILE_M}) AS BIGINT))) AS gy(cy)"""
)
W_INC = con.execute("SELECT sum(w) FROM incid").fetchone()[0]  # bytes physically stored by the tiling
print(f"tiled (replicated) bytes at {TILE_M/1000:.0f} km: {W_INC/1e9:.3f} GB "
      f"({W_INC/W_SEG:.2f}x replication)")

def candidate_share(x0, y0, x1, y1):
    cx0, cx1 = int(x0 // TILE_M), int(x1 // TILE_M)
    cy0, cy1 = int(y0 // TILE_M), int(y1 // TILE_M)
    tw, tc = con.execute(
        f"""SELECT COALESCE(sum(w),0), count(DISTINCT (cx,cy)) FROM incid
            WHERE cx BETWEEN {cx0} AND {cx1} AND cy BETWEEN {cy0} AND {cy1}"""
    ).fetchone()
    return 100.0 * tw / W_INC, int(tc)

def local_density(x0, y0, x1, y1):
    n, b = con.execute(
        f"""SELECT count(*), COALESCE(sum(w),0) FROM bounds
            WHERE cx0 BETWEEN {x0} AND {x1} AND cy0 BETWEEN {y0} AND {y1}"""
    ).fetchone()
    return int(n), int(b)

# =================================================== (A) systematic density-vs-pruning scatter
# Fixed-size query box centred on every non-empty tile.
cells = con.execute(
    f"""SELECT DISTINCT CAST(floor(cx0/{TILE_M}) AS BIGINT) AS gx,
                        CAST(floor(cy0/{TILE_M}) AS BIGINT) AS gy
        FROM bounds"""
).fetchall()
print(f"non-empty {TILE_M/1000:.0f} km tiles: {len(cells)}")

half = QUERY_BOX_M / 2.0
scatter = []
for gx, gy in cells:
    ccx = (gx + 0.5) * TILE_M
    ccy = (gy + 0.5) * TILE_M
    x0, y0, x1, y1 = ccx - half, ccy - half, ccx + half, ccy + half
    n_seg, b_seg = local_density(x0, y0, x1, y1)
    if n_seg == 0:
        continue
    share, _ = candidate_share(x0, y0, x1, y1)
    scatter.append({"gx": gx, "gy": gy, "cx": ccx, "cy": ccy,
                    "density_segments": n_seg, "density_bytes": b_seg,
                    "candidate_share_pct": round(share, 4)})

scatter.sort(key=lambda r: r["density_segments"])

def _mean(rows, key):
    return sum(r[key] for r in rows) / len(rows) if rows else float("nan")

# Self-calibrating characterization: split the query boxes into density deciles
# and report the mean candidate share in each. Works for any density
# distribution (no fixed threshold), and it is the pruning-vs-traffic-density
# curve.
n = len(scatter)
deciles = []
for d in range(10):
    lo_i = d * n // 10
    hi_i = (d + 1) * n // 10
    chunk = scatter[lo_i:hi_i]
    if not chunk:
        continue
    deciles.append({
        "decile": d + 1,
        "density_min": chunk[0]["density_segments"],
        "density_max": chunk[-1]["density_segments"],
        "mean_candidate_share_pct": round(_mean(chunk, "candidate_share_pct"), 4),
        "n": len(chunk),
    })

open_share = deciles[0]["mean_candidate_share_pct"] if deciles else float("nan")
corr_share = deciles[-1]["mean_candidate_share_pct"] if deciles else float("nan")
open_rows = scatter[: n // 10]
corr_rows = scatter[9 * n // 10:]
print("\n(A) Pruning vs traffic density  (fixed {:.0f} km query box, {:.0f} km tiling):"
      .format(QUERY_BOX_M/1000, TILE_M/1000))
print("    decile  density(seg)   mean candidate share")
for dd in deciles:
    print(f"      {dd['decile']:>2}    {dd['density_min']:>6}-{dd['density_max']:<6}   "
          f"{dd['mean_candidate_share_pct']:.3f}%   (n={dd['n']})")
print(f"    -> sparsest decile reads {open_share:.3f}% ; densest decile reads {corr_share:.3f}% "
      f"of tiled bytes")

# ================================================================ (B) named-region table
named = []
for name, (x0, y0, x1, y1) in CORRIDOR_REGIONS.items():
    share, tc = candidate_share(x0, y0, x1, y1)
    n_seg, _ = local_density(x0, y0, x1, y1)
    named.append({"region": name, "kind": "corridor",
                  "area_km2": round((x1-x0)*(y1-y0)/1e6, 3),
                  "local_segments": n_seg,
                  "candidate_share_pct": round(share, 4), "tiles_touched": tc})

belt_w = CORRIDOR_REGIONS["belt (corridor)"][2] - CORRIDOR_REGIONS["belt (corridor)"][0]
belt_h = CORRIDOR_REGIONS["belt (corridor)"][3] - CORRIDOR_REGIONS["belt (corridor)"][1]
if OPEN_WATER_REGIONS:
    # Use the open-water regions you supplied (recommended: you know the map).
    for name, (x0, y0, x1, y1) in OPEN_WATER_REGIONS.items():
        share, tc = candidate_share(x0, y0, x1, y1)
        n_seg, _ = local_density(x0, y0, x1, y1)
        named.append({"region": name, "kind": "open_water",
                      "area_km2": round((x1-x0)*(y1-y0)/1e6, 3),
                      "local_segments": n_seg,
                      "candidate_share_pct": round(share, 4), "tiles_touched": tc})
else:
    # Fallback: auto-pick belt-sized boxes in low-density (but non-trivial)
    # non-empty tiles, spaced apart. SANITY-CHECK these on a map: confirm each is
    # genuine open sea, not a data-sparse corner of a busy area.
    min_open = max(5, deciles[1]["density_min"] if len(deciles) > 1 else 5)
    picked = []
    for r in scatter:  # already sorted ascending by density
        if len(picked) >= N_OPEN_REGIONS:
            break
        if r["density_segments"] < min_open:
            continue
        if any(abs(r["gx"] - p["gx"]) <= 1 and abs(r["gy"] - p["gy"]) <= 1 for p in picked):
            continue
        picked.append(r)
    for i, r in enumerate(picked, 1):
        x0, y0 = r["cx"] - belt_w/2, r["cy"] - belt_h/2
        x1, y1 = r["cx"] + belt_w/2, r["cy"] + belt_h/2
        share, tc = candidate_share(x0, y0, x1, y1)
        n_seg, _ = local_density(x0, y0, x1, y1)
        named.append({"region": f"open_water_{i} (auto)", "kind": "open_water",
                      "area_km2": round(belt_w*belt_h/1e6, 3),
                      "local_segments": n_seg,
                      "candidate_share_pct": round(share, 4), "tiles_touched": tc})

print("\n(B) Named regions (belt-sized open-water boxes auto-selected):")
for r in named:
    print(f"    {r['region']:20s} {r['kind']:10s} "
          f"density={r['local_segments']:>7d} seg   "
          f"reads {r['candidate_share_pct']:.3f}% of tiled bytes")

# ------------------------------------------------------------------------------- outputs
with open("regime_analysis.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(named[0].keys()))
    w.writeheader()
    w.writerows(named)

json.dump({"l0_segments": N_SEG, "l0_bytes": W_SEG, "tiled_bytes": W_INC,
           "tile_m": TILE_M, "query_box_m": QUERY_BOX_M,
           "sparsest_decile_share_pct": open_share,
           "densest_decile_share_pct": corr_share,
           "deciles": deciles, "named": named, "scatter": scatter},
          open("regime_analysis.json", "w"), indent=2)

# figure: density vs candidate share, corridor and open-water regimes shaded
fig, ax = plt.subplots(figsize=(7, 5))
xs = [max(r["density_segments"], 1) for r in scatter]
ys = [max(r["candidate_share_pct"], 1e-3) for r in scatter]
ax.scatter(xs, ys, s=10, alpha=0.35, color="#4292c6", label="query box on a tile")
if corr_rows:
    ax.scatter([r["density_segments"] for r in corr_rows],
               [max(r["candidate_share_pct"], 1e-3) for r in corr_rows],
               s=14, alpha=0.7, color="#08519c", label=f"corridor (>= {DENSITY_QUANTILE_HIGH:.0%})")
if open_rows:
    ax.scatter([r["density_segments"] for r in open_rows],
               [max(r["candidate_share_pct"], 1e-3) for r in open_rows],
               s=14, alpha=0.7, color="#d6604d", label=f"open water (<= {DENSITY_QUANTILE_LOW:.0%})")
ax.set_xscale("log")
ax.set_yscale("log")
ax.set_xlabel(f"local traffic density (segments in a {QUERY_BOX_M/1000:.0f} km query box)")
ax.set_ylabel(f"candidate byte-share a pruner must read (%), {TILE_M/1000:.0f} km tiling")
ax.set_title("Pruning benefit vs traffic density (from-metadata, no rebuild)")
ax.legend(fontsize=8)
ax.spines[["top", "right"]].set_visible(False)
plt.tight_layout()
os.makedirs("figures", exist_ok=True)
plt.savefig("figures/eval_regime.png", dpi=150)
print("\nwrote regime_analysis.csv / .json / figures/eval_regime.png")
