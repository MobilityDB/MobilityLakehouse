#!/usr/bin/env python3
# Raw AIS message density per region, for the Dataset chapter
import json
import os
import sys

import duckdb

HERE = os.environ.get("LAKEHOUSE_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".."))  # benchmark/
os.chdir(HERE)
sys.path.insert(0, HERE)

from lakehouse.query.registry import METRIC_EPSG

RAWGLOB = "data/raw/*.parquet"
DAYS = 31

# 14 km probe boxes, centres verbatim from regime_queries.ipynb (EPSG:32632).
BOX_SIDE_M = 14_000
CENTERS = {
    "belt":            ("corridor",   "R{\\o}dby--Puttgarden belt",  647415.0, 6050358.0),
    "goteborg":        ("corridor",   "G{\\\"o}teborg approach",     672854.0, 6397901.0),
    "oresund":         ("corridor",   "{\\O}resund",                 725000.0, 6175000.0),
    "north_sea_west":  ("open water", "North Sea (west)",            249317.0, 6191062.0),
    "north_sea_south": ("open water", "North Sea (south)",           273380.0, 6044792.0),
    "skagerrak":       ("open water", "Skagerrak",                   517683.0, 6439882.0),
    "kattegat":        ("open water", "Kattegat",                    635062.0, 6275026.0),
}

def regions():
    h = BOX_SIDE_M / 2.0
    for key, (kind, label, cx, cy) in CENTERS.items():
        yield key, kind, label, cx - h, cy - h, cx + h, cy + h

def envelope(con, xmin, ymin, xmax, ymax, pad_deg=0.02):
    n = 25
    pts = []
    for i in range(n + 1):
        f = i / n
        pts += [(xmin + f * (xmax - xmin), ymin), (xmin + f * (xmax - xmin), ymax),
                (xmin, ymin + f * (ymax - ymin)), (xmax, ymin + f * (ymax - ymin))]
    vals = ",".join(f"({x},{y})" for x, y in pts)
    lo0, lo1, la0, la1 = con.execute(f"""
        WITH p(x, y) AS (VALUES {vals}), g AS (
          SELECT ST_Transform(ST_Point(x, y), 'EPSG:{METRIC_EPSG}', 'EPSG:4326',
                              always_xy := true) AS pt FROM p)
        SELECT min(ST_X(pt)), max(ST_X(pt)), min(ST_Y(pt)), max(ST_Y(pt)) FROM g""").fetchone()
    return lo0 - pad_deg, lo1 + pad_deg, la0 - pad_deg, la1 + pad_deg

def compute(con=None):
    own = con is None
    if own:
        con = duckdb.connect()
        con.execute("PRAGMA threads=4")
    con.execute("INSTALL spatial; LOAD spatial;")

    total = con.execute(f"SELECT count(*) FROM read_parquet('{RAWGLOB}')").fetchone()[0]
    rows = []
    for key, kind, label, xmin, ymin, xmax, ymax in regions():
        lo0, lo1, la0, la1 = envelope(con, xmin, ymin, xmax, ymax)
        # Step 1: cheap lon/lat prefilter, materialised so the reprojection in
        # step 2 provably runs on the survivors only, not on all 485 M rows.
        con.execute(f"""CREATE OR REPLACE TEMP TABLE cand AS
            SELECT longitude AS lon, latitude AS lat, mmsi
            FROM read_parquet('{RAWGLOB}')
            WHERE longitude BETWEEN {lo0} AND {lo1}
              AND latitude  BETWEEN {la0} AND {la1}""")
        # Step 2: exact test in the metric CRS the box is defined in.
        n, v = con.execute(f"""
            WITH t AS (
              SELECT mmsi, ST_Transform(ST_Point(lon, lat), 'EPSG:4326',
                     'EPSG:{METRIC_EPSG}', always_xy := true) AS p FROM cand)
            SELECT count(*), count(DISTINCT mmsi) FROM t
            WHERE ST_X(p) BETWEEN {xmin} AND {xmax}
              AND ST_Y(p) BETWEEN {ymin} AND {ymax}""").fetchone()
        area = (xmax - xmin) * (ymax - ymin) / 1e6
        rows.append({
            "region": key, "kind": kind, "label": label,
            "bbox_m": [xmin, ymin, xmax, ymax], "area_km2": round(area, 3),
            "messages": int(n), "vessels": int(v),
            "msg_per_km2": round(n / area, 1),
            "msg_per_km2_per_day": round(n / area / DAYS, 1),
            "share_of_feed_pct": round(100.0 * n / total, 4),
        })
        print(f"  {key:<16} {kind:<12} {n:>12,} msgs  {n/area:>12,.0f}/km²  "
              f"{v:>5} vessels", flush=True)

    out = {
        "raw_total_messages": int(total),
        "days": DAYS,
        "metric_epsg": METRIC_EPSG,
        "probe_box_side_m": BOX_SIDE_M,
        "probe_box_area_km2": round(BOX_SIDE_M ** 2 / 1e6, 1),
        "regions": rows,
    }
    out.update(summarise(rows))
    if own:
        con.close()
    return out

def summarise(rows):
    def med(vals):
        v = sorted(vals)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0

    cor = [r["msg_per_km2"] for r in rows if r["kind"] == "corridor"]
    opn = [r["msg_per_km2"] for r in rows if r["kind"] == "open water"]
    ratios = [c / o for c in cor for o in opn if o > 0]
    return {
        "corridor_median_msg_per_km2": round(med(cor), 1),
        "open_water_median_msg_per_km2": round(med(opn), 1),
        "median_ratio": round(med(cor) / med(opn), 1),
        "ratio_min": round(min(ratios), 1),
        "ratio_max": round(max(ratios), 1),
        "corridor_share_of_feed_pct": round(
            sum(r["share_of_feed_pct"] for r in rows if r["kind"] == "corridor"), 2),
        "open_water_share_of_feed_pct": round(
            sum(r["share_of_feed_pct"] for r in rows if r["kind"] == "open water"), 4),
    }
if __name__ == "__main__":
    # --reuse re-renders the table from the last run's JSON (formatting changes
    # shouldn't cost another full scan of the 11 GB raw feed)
    if "--reuse" in sys.argv and os.path.exists("region_density.json"):
        res = json.load(open("region_density.json"))
        res.pop("corridor_vs_open_water_ratio", None)   # superseded by summarise()
        # keep only regions still defined here, and refresh their labels: the set
        # and the wording both changed after the first run
        labels = {k: lab for k, _, lab, *_ in regions()}
        res["regions"] = [r for r in res["regions"] if r["region"] in labels]
        for r in res["regions"]:
            r["label"] = labels[r["region"]]
        res.update(summarise(res["regions"]))
    else:
        print("raw message density per region...")
        res = compute()

    json.dump(res, open("region_density.json", "w"), indent=2)
    print("\nwrote region_density.json")

    if os.path.exists("dataset_stats.json"):
        S = json.load(open("dataset_stats.json"))
        S["region_density"] = res
        json.dump(S, open("dataset_stats.json", "w"), indent=2, default=str)
        print("merged into dataset_stats.json (key: region_density)")

    print(f"\ncorridor median {res['corridor_median_msg_per_km2']:,}/km² vs open water "
          f"{res['open_water_median_msg_per_km2']:,}/km² -> {res['median_ratio']}x "
          f"(per-pair range {res['ratio_min']}x .. {res['ratio_max']}x)")
    print(f"share of the raw feed: corridor boxes {res['corridor_share_of_feed_pct']}%, "
          f"open-water boxes {res['open_water_share_of_feed_pct']}%")
