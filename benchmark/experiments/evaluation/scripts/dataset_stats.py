#!/usr/bin/env python3
# Dataset characterisation for the thesis "Dataset" chapter
import os, glob, json, tempfile
import duckdb
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.environ.get("LAKEHOUSE_ROOT") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".."))  # benchmark/
os.chdir(HERE)
FIG = "figures"
os.makedirs(FIG, exist_ok=True)

RAW = sorted(glob.glob("data/raw/*.parquet"))
assert RAW, "no data/raw/*.parquet found - run from benchmark/"
RAWGLOB = "data/raw/*.parquet"

L0 = None
for cand in ("data/trips/L0/L0", "data/trips/L0", "data/L0/L0"):
    if glob.glob(cand + "/**/*.parquet", recursive=True):
        L0 = cand + "/**/*.parquet"
        break

con = duckdb.connect()
con.execute("PRAGMA threads=4")
S = {}

def fig_done(name):
    plt.gca().spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.savefig(f"{FIG}/{name}", dpi=150)
    plt.close()
    print("  wrote", f"{FIG}/{name}")

# ---------- raw totals ----------
print("raw totals...")
S["raw_files"] = len(RAW)
S["raw_total_bytes"] = sum(os.path.getsize(f) for f in RAW)
S["raw_total_gb"] = round(S["raw_total_bytes"] / 1e9, 2)
S["raw_total_messages"] = con.execute(
    f"SELECT count(*) FROM read_parquet('{RAWGLOB}')").fetchone()[0]
S["distinct_vessels_raw"] = con.execute(
    f"SELECT count(DISTINCT mmsi) FROM read_parquet('{RAWGLOB}')").fetchone()[0]
dmin, dmax = con.execute(
    f"SELECT min(event_time), max(event_time) FROM read_parquet('{RAWGLOB}')").fetchone()
S["date_min"], S["date_max"] = str(dmin), str(dmax)

# ---------- messages per day ----------
print("per-day volume...")
perday = con.execute(f"""
    SELECT CAST(event_time AS DATE) d, count(*) n
    FROM read_parquet('{RAWGLOB}') WHERE event_time IS NOT NULL
    GROUP BY 1 ORDER BY 1""").fetchall()
perday = [(str(d), int(n)) for d, n in perday]
S["messages_per_day"] = dict(perday)
days = [d for d, _ in perday]
ns = [n for _, n in perday]
plt.figure(figsize=(12, 3.4))
plt.bar(range(len(days)), ns, color="#4292c6")
plt.xticks(range(len(days)), [d[-2:] for d in days], fontsize=7)
plt.ylabel("AIS messages"); plt.xlabel("day of January 2026")
plt.title("Raw AIS message volume per day")
fig_done("ds_messages_per_day.png")

# ---------- ship types ----------
print("ship types...")
st = con.execute(f"""
    SELECT COALESCE(ship_type,'(unknown)') t, count(*) n, count(DISTINCT mmsi) v
    FROM read_parquet('{RAWGLOB}') GROUP BY 1 ORDER BY n DESC""").fetchall()
S["ship_type_messages"] = {t: int(n) for t, n, _ in st}
S["ship_type_vessels"] = {t: int(v) for t, _, v in st}
top = st[:12][::-1]
plt.figure(figsize=(8, 5))
plt.barh([t for t, _, _ in top], [v for _, _, v in top], color="#08519c")
plt.xlabel("distinct vessels"); plt.title("Vessels by ship type (top 12)")
fig_done("ds_ship_types.png")

# ---------- mobile type (Class A / B / ...) ----------
S["mobile_type_messages"] = {
    t: int(n) for t, n in con.execute(f"""
        SELECT COALESCE(mobile_type,'(unknown)') t, count(*) n
        FROM read_parquet('{RAWGLOB}') GROUP BY 1 ORDER BY n DESC""").fetchall()}

# ---------- navigational status ----------
print("nav status...")
nstat = con.execute(f"""
    SELECT COALESCE(navigational_status,'(unknown)') s, count(*) n
    FROM read_parquet('{RAWGLOB}') GROUP BY 1 ORDER BY n DESC""").fetchall()
S["nav_status_messages"] = {s: int(n) for s, n in nstat}
topn = nstat[:10][::-1]
plt.figure(figsize=(8, 5))
plt.barh([s[:30] for s, _ in topn], [n for _, n in topn], color="#6baed6")
plt.xlabel("messages"); plt.title("Navigational status (top 10)")
fig_done("ds_nav_status.png")

# ---------- spatial extent + density ----------
print("spatial density...")
lon0, lon1, lat0, lat1 = con.execute(f"""
    SELECT min(longitude), max(longitude), min(latitude), max(latitude)
    FROM read_parquet('{RAWGLOB}')
    WHERE longitude BETWEEN -20 AND 40 AND latitude BETWEEN 40 AND 75""").fetchone()
S.update(lon_min=lon0, lon_max=lon1, lat_min=lat0, lat_max=lat1)
heat = con.execute(f"""
    SELECT round(longitude,2) lon, round(latitude,2) lat, count(*) n
    FROM read_parquet('{RAWGLOB}')
    WHERE longitude BETWEEN 3 AND 18 AND latitude BETWEEN 53 AND 60
    GROUP BY 1,2""").fetchall()
if heat:
    lon = np.array([h[0] for h in heat]); lat = np.array([h[1] for h in heat])
    n = np.array([h[2] for h in heat], float)
    plt.figure(figsize=(7, 6))
    plt.scatter(lon, lat, c=np.log10(n), s=4, cmap="inferno")
    plt.colorbar(label="log10(messages)")
    plt.xlabel("longitude"); plt.ylabel("latitude")
    plt.title("AIS traffic density, Danish waters (Jan 2026)")
    fig_done("ds_spatial_heatmap.png")

# ---------- reporting interval (one mid-month day, Class A) ----------
print("reporting interval...")
try:
    gaps = con.execute(f"""
        WITH s AS (
          SELECT mmsi, event_time,
                 lag(event_time) OVER (PARTITION BY mmsi ORDER BY event_time) prev
          FROM read_parquet('{RAW[len(RAW)//2]}')
          WHERE mobile_type = 'Class A' AND event_time IS NOT NULL)
        SELECT epoch(event_time) - epoch(prev) g
        FROM s WHERE prev IS NOT NULL AND event_time > prev""").fetchdf()
    g = gaps["g"].values.astype(float); g = g[(g > 0) & (g < 3600)]
    S["report_interval_median_s"] = float(np.median(g))
    S["report_interval_p90_s"] = float(np.percentile(g, 90))
    plt.figure(figsize=(7, 4))
    plt.hist(g, bins=np.logspace(0, np.log10(3600), 50), color="#4292c6")
    plt.xscale("log")
    plt.xlabel("seconds between consecutive messages (Class A)")
    plt.ylabel("count"); plt.title("AIS reporting interval (one day)")
    fig_done("ds_reporting_interval.png")
except Exception as e:
    print("  interval skipped:", e)

# ---------- CSV vs Parquet (one day, same records) ----------
print("csv vs parquet...")
try:
    day = RAW[len(RAW) // 2]
    pbytes = os.path.getsize(day)
    tmp = os.path.join(tempfile.gettempdir(), "oneday_ais.csv")
    con.execute(f"COPY (SELECT * FROM read_parquet('{day}')) TO '{tmp}' (FORMAT CSV, HEADER)")
    cbytes = os.path.getsize(tmp); os.remove(tmp)
    S.update(oneday_file=os.path.basename(day), parquet_bytes_oneday=pbytes,
             csv_bytes_oneday=cbytes, csv_parquet_ratio=round(cbytes / pbytes, 1))
    plt.figure(figsize=(4.6, 4))
    plt.bar(["CSV", "Parquet\n(ZSTD)"], [cbytes / 1e6, pbytes / 1e6],
            color=["#bdbdbd", "#08519c"])
    for i, v in enumerate([cbytes / 1e6, pbytes / 1e6]):
        plt.text(i, v, f"{v:.0f} MB", ha="center", va="bottom")
    plt.ylabel("size of one day (MB)")
    plt.title(f"Same records: CSV vs Parquet ({cbytes/pbytes:.1f}x smaller)")
    fig_done("ds_csv_vs_parquet.png")
except Exception as e:
    print("  csv/parquet skipped:", e)

# ---------- data quality: raw anomaly counts (same one day) ----------
# Grounds the "Data Quality" paragraphs in the Dataset chapter (sec:ds-clean).
print("data quality (one day)...")
try:
    day = RAW[len(RAW) // 2]
    dq = {"file": os.path.basename(day)}
    row = con.execute(f"""
        SELECT
          count(*)                                                        AS total,
          sum(CASE WHEN event_time IS NULL THEN 1 ELSE 0 END)             AS null_time,
          sum(CASE WHEN latitude IS NULL OR longitude IS NULL
              THEN 1 ELSE 0 END)                                          AS null_pos,
          sum(CASE WHEN latitude = 91 OR longitude = 181
              THEN 1 ELSE 0 END)                                          AS sentinel_pos,
          sum(CASE WHEN latitude NOT BETWEEN -90 AND 90
                    OR longitude NOT BETWEEN -180 AND 180
              THEN 1 ELSE 0 END)                                          AS out_of_range_pos,
          sum(CASE WHEN NOT regexp_matches(CAST(mmsi AS VARCHAR), '^[0-9]{{9}}$')
              THEN 1 ELSE 0 END)                                          AS invalid_mmsi,
          sum(CASE WHEN mobile_type = 'Class A'
                    AND NOT regexp_matches(CAST(mmsi AS VARCHAR), '^[0-9]{{9}}$')
              THEN 1 ELSE 0 END)                                          AS invalid_mmsi_class_a,
          sum(CASE WHEN sog > 100 THEN 1 ELSE 0 END)                      AS sog_gt_100kn,
          sum(CASE WHEN width >= 75 THEN 1 ELSE 0 END)                    AS width_ge_75m,
          sum(CASE WHEN length >= 488 THEN 1 ELSE 0 END)                  AS length_ge_488m,
          sum(CASE WHEN draught >= 28.5 THEN 1 ELSE 0 END)                AS draught_ge_28_5m
        FROM read_parquet('{day}')""").fetchone()
    keys = ["total", "null_time", "null_pos", "sentinel_pos", "out_of_range_pos",
            "invalid_mmsi", "invalid_mmsi_class_a", "sog_gt_100kn",
            "width_ge_75m", "length_ge_488m", "draught_ge_28_5m"]
    dq.update({k: int(v) for k, v in zip(keys, row)})

    # duplicates on (mmsi, event_time): same broadcast heard by several receivers
    dq["duplicate_mmsi_time"] = int(con.execute(f"""
        SELECT count(*) - count(DISTINCT (mmsi, event_time))
        FROM read_parquet('{day}')""").fetchone()[0])
    dq["duplicate_pct"] = round(100.0 * dq["duplicate_mmsi_time"] / dq["total"], 1)
    dq["invalid_mmsi_pct"] = round(100.0 * dq["invalid_mmsi"] / dq["total"], 1)

    # GPS "teleports": consecutive Class A pairs implying > 100 kn
    # (haversine over deduped, valid-MMSI, in-range Class A points; mirrors
    # the impossible_jump rule in the Silver cleaning stage)
    trans, jumps = con.execute(f"""
        WITH deduped AS (
          SELECT mmsi, event_time, latitude, longitude
          FROM (
            SELECT mmsi, event_time, latitude, longitude,
                   row_number() OVER (PARTITION BY mmsi, event_time
                                      ORDER BY latitude, longitude) rn
            FROM read_parquet('{day}')
            WHERE mobile_type = 'Class A'
              AND regexp_matches(CAST(mmsi AS VARCHAR), '^[0-9]{{9}}$')
              AND latitude BETWEEN -90 AND 90
              AND longitude BETWEEN -180 AND 180
          ) WHERE rn = 1
        ), steps AS (
          SELECT
            2 * 6371000 * asin(sqrt(
              sin(radians(latitude - lag(latitude) OVER w) / 2) ^ 2 +
              cos(radians(lag(latitude) OVER w)) * cos(radians(latitude)) *
              sin(radians(longitude - lag(longitude) OVER w) / 2) ^ 2
            )) / NULLIF(epoch(event_time - lag(event_time) OVER w), 0)
               * 1.94384 AS knots
          FROM deduped
          WINDOW w AS (PARTITION BY mmsi ORDER BY event_time)
        )
        SELECT count(*), sum(CASE WHEN knots > 100 THEN 1 ELSE 0 END)
        FROM steps WHERE knots IS NOT NULL""").fetchone()
    dq["class_a_transitions"] = int(trans)
    dq["implied_speed_gt_100kn"] = int(jumps)

    S["data_quality_oneday"] = dq
    for k, v in dq.items():
        print(f"  {k}: {v}")
except Exception as e:
    print("  data quality skipped:", e)

# ---------- cleaned L0 segments ----------
if L0:
    print("cleaned segments...")
    seg = con.execute(f"""
        SELECT segment_type, count(*) n, count(DISTINCT mmsi) v,
               median(epoch(tmax) - epoch(tmin)) dur
        FROM read_parquet('{L0}') GROUP BY 1 ORDER BY n DESC""").fetchall()
    S["segments"] = {str(r[0]): {"n": int(r[1]), "vessels": int(r[2]),
                                 "median_dur_s": float(r[3])} for r in seg}
    S["seg_total"] = int(sum(r[1] for r in seg))
    S["distinct_vessels_clean"] = con.execute(
        f"SELECT count(DISTINCT mmsi) FROM read_parquet('{L0}')").fetchone()[0]

    dur = con.execute(f"""
        SELECT segment_type, (epoch(tmax) - epoch(tmin)) / 3600.0 h
        FROM read_parquet('{L0}') WHERE tmax > tmin""").fetchdf()
    palette = {"stop": "#d6604d", "move": "#4393c3", "trip": "#4393c3",
               "stationary": "#d6604d", "moving": "#4393c3", "in motion": "#4393c3"}
    plt.figure(figsize=(7, 4))
    bins = np.linspace(0, 24, 41)
    for t in dur["segment_type"].dropna().unique():
        d = dur[dur.segment_type == t]["h"].values
        d = d[(d > 0) & (d < 24)]
        if len(d):
            plt.hist(d, bins=bins, histtype="step", linewidth=1.8, label=str(t),
                     color=palette.get(str(t), "#888888"))
    plt.xlabel("segment duration (hours)"); plt.ylabel("count (log scale)")
    plt.xlim(0, 24); plt.yscale("log")
    plt.legend(); plt.title("Cleaned segment durations by type")
    fig_done("ds_segment_duration.png")

    plt.figure(figsize=(5, 4))
    vals = [S["raw_total_messages"], S["seg_total"]]
    plt.bar(["raw AIS\nmessages", "cleaned\nsegments"], vals,
            color=["#bdbdbd", "#08519c"])
    plt.yscale("log")
    for i, v in enumerate(vals):
        plt.text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=9)
    plt.ylabel("count (log scale)")
    plt.title(f"Raw to segments ({S['raw_total_messages']/max(S['seg_total'],1):.0f}x reduction)")
    fig_done("ds_reduction.png")
else:
    print("WARNING: cleaned L0 not found locally; segment stats skipped.")

# ---------- raw density per study region (Table tab:ds-region-density) ----------
# Exact counts behind the chapter's "density is far from uniform" claim, over the
# same regions the evaluation queries. Reprojects candidate points, so it needs
# the spatial extension; kept in its own module because it is also run alone.
print("region density...")
try:
    from region_density import compute as region_density
    S["region_density"] = region_density(con)
except Exception as e:
    print("  region density skipped:", e)

json.dump(S, open("dataset_stats.json", "w"), indent=2, default=str)
print("\n==== dataset_stats.json written ====")
for k, v in S.items():
    if not isinstance(v, dict):
        print(f"  {k}: {v}")
print("\nfigures:", sorted(os.path.basename(p) for p in glob.glob(f"{FIG}/ds_*.png")))
