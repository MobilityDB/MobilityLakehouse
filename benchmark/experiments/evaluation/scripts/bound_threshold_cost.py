import os
# What do the minimum-instants thresholds and the phantom guard actually cost?
import duckdb
from pathlib import Path

CLI = Path(os.getenv("LAKEHOUSE_ROOT")
           or Path(__file__).resolve().parents[3])
OLD = os.environ.get("MOBILITYDUCK_EXT_BASELINE", "")
L0 = (CLI / 'data/L0/L0/year=2026/month=01/day=2026-01-15.parquet').as_posix()
REGION_M, TILE_M, SEGS = 50_000.0, 1000.0, 16

con = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
con.execute('PRAGMA threads=4;')
con.execute('INSTALL spatial; LOAD spatial;')
con.execute(f"LOAD '{OLD}';")
con.execute(f"""
    CREATE OR REPLACE TEMP VIEW base_segments AS
    SELECT * EXCLUDE (traj_wkb, geometry, bbox),
           bbox.xmin AS bbox_min_x, bbox.ymin AS bbox_min_y,
           bbox.xmax AS bbox_max_x, bbox.ymax AS bbox_max_y,
           tgeompointFromBinary(traj_wkb) AS traj
    FROM read_parquet('{L0}', hive_partitioning=true);
""")

print('=== L2 (spaceSplit, 1 km tiles) over all in-motion segments, unguarded ===')
con.execute(f"""
    CREATE OR REPLACE TEMP TABLE l2 AS
    SELECT bs.mmsi, bs.start_time, bs.end_time,
           numInstants(sp.tpoint) AS n,
           startTimestamp(sp.tpoint) AS t0, endTimestamp(sp.tpoint) AS t1
    FROM (SELECT * FROM base_segments WHERE segment_type = 'in motion') bs,
         LATERAL spaceSplit(bs.traj, {TILE_M}, {TILE_M}, 1.0,
                            ST_Point(0,0), FALSE) sp(spaceBin, tpoint);
""")
print(con.execute("""
    SELECT count(*) AS tiles, sum(n) AS instants, count(DISTINCT mmsi) AS vessels,
           sum((n=1)::INT) AS n1, sum((n=2)::INT) AS n2,
           sum(CASE WHEN n<2 THEN n ELSE 0 END) AS pts_lost_ge2,
           sum(CASE WHEN n<3 THEN n ELSE 0 END) AS pts_lost_ge3
    FROM l2""").fetchdf().to_string())
tot = con.execute('SELECT count(*), sum(n) FROM l2').fetchone()
for thr in (1, 2, 3):
    r = con.execute(f"""
        SELECT count(*), sum(n), count(DISTINCT mmsi) FROM l2 WHERE n >= {thr}""").fetchone()
    print(f'  numInstants >= {thr}:  tiles {r[0]:>7} ({100*r[0]/tot[0]:5.2f}%)  '
          f'instants {r[1]:>9} ({100*r[1]/tot[1]:5.2f}%)  vessels {r[2]}')

print('\n=== phantom-instant guard: rows whose extent escapes the parent segment ===')
# _temporal_within_parent exists to drop these. Session TZ is the system default,
# exactly as the pipeline builds, so the naive sidecar and the 1h-early traj EWKB
# sit on the same clock here.
print(con.execute("""
    SELECT count(*) AS tiles_total,
           sum((t0 < start_time)::INT) AS starts_before_parent,
           sum((t1 > end_time)::INT)   AS ends_after_parent,
           min(t0) AS earliest_tile_start
    FROM l2""").fetchdf().to_string())

print('\n=== L3 (MEST) stage-1 50 km region clip: cost of the >=3 threshold ===')
con.execute(f"""
    CREATE OR REPLACE TEMP TABLE clips AS
    SELECT mmsi, numInstants(traj) AS n FROM (
        SELECT mmsi, UNNEST(sequences(atStbox(traj,
                   ST_MakeEnvelope(rx*{REGION_M}, ry*{REGION_M},
                                   (rx+1)*{REGION_M}, (ry+1)*{REGION_M})::STBOX,
                   true))) AS traj
        FROM (SELECT * FROM base_segments WHERE segment_type = 'in motion') bs,
             LATERAL (SELECT DISTINCT
                 floor(bs.bbox_min_x/{REGION_M})::INT AS rx,
                 floor(bs.bbox_min_y/{REGION_M})::INT AS ry) c
    ) WHERE traj IS NOT NULL;
""")
t = con.execute('SELECT count(*), sum(n) FROM clips').fetchone()
for thr in (1, 2, 3):
    r = con.execute(f'SELECT count(*), sum(n) FROM clips WHERE n >= {thr}').fetchone()
    print(f'  numInstants >= {thr}:  clips {r[0]:>6} ({100*r[0]/t[0]:5.2f}%)  '
          f'instants {r[1]:>9} ({100*r[1]/t[1]:5.2f}%)')
