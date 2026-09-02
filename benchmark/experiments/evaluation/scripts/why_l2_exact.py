import os
# Why is L2 exact and L3 not, when both tile?
import duckdb

OLD = os.environ.get("MOBILITYDUCK_EXT_BASELINE", "")
L0 = 'data/L0/L0/year=2026/month=01/day=2026-01-15.parquet'
REGION_M, TILE_M, SEGS = 50_000.0, 1000.0, 16
rx, ry = 13, 120
x0, y0 = rx * REGION_M, ry * REGION_M
x1, y1 = x0 + REGION_M, y0 + REGION_M

con = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
con.execute('SET enable_progress_bar=false;')
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
SRC = f"""
    SELECT * FROM (
        SELECT start_time, end_time,
               UNNEST(sequences(atStbox(traj,
                   ST_MakeEnvelope({x0},{y0},{x1},{y1})::STBOX, true))) AS traj
        FROM base_segments
        WHERE segment_type = 'in motion'
          AND bbox_max_x >= {x0} AND bbox_min_x < {x1}
          AND bbox_max_y >= {y0} AND bbox_min_y < {y1}
    ) WHERE traj IS NOT NULL
"""

print("=== L2: the cell key is spaceSplit's spaceBin (analytic grid) ===")
r = con.execute(f"""
    SELECT count(*) AS n,
           count(*) FILTER (WHERE ST_X(sp.spaceBin) = round(ST_X(sp.spaceBin)/{TILE_M})*{TILE_M}
                              AND ST_Y(sp.spaceBin) = round(ST_Y(sp.spaceBin)/{TILE_M})*{TILE_M}) AS on_grid,
           max(abs(ST_X(sp.spaceBin) - round(ST_X(sp.spaceBin)/{TILE_M})*{TILE_M})) AS max_dev
    FROM ({SRC}) bs,
         LATERAL spaceSplit(bs.traj, {TILE_M}, {TILE_M}, 1.0, ST_Point(0,0), FALSE) sp(spaceBin, tpoint)
""").fetchone()
print(f"  tiles={r[0]}  keys exactly on the grid: {r[1]}  max deviation: {r[2]}")

print("\n=== L3: the cell key is Xmin() of a MEST box (measured off clipped geometry) ===")
r = con.execute(f"""
    SELECT count(*) AS n,
           count(*) FILTER (WHERE Xmin(mb.box) = round(Xmin(mb.box)/{TILE_M})*{TILE_M}) AS on_grid,
           count(*) FILTER (WHERE Xmin(mb.box) < {x0} OR Ymin(mb.box) < {y0}) AS below_cell,
           min(Xmin(mb.box)) AS min_xmin
    FROM ({SRC}) bs,
         LATERAL UNNEST(splitEachNStboxes(bs.traj, {SEGS})) AS mb(box)
""").fetchone()
print(f"  boxes={r[0]}  keys on any grid: {r[1]}  keys below the cell origin: {r[2]}")
print(f"  min Xmin observed: {r[3]!r}   (cell origin is exactly {x0})")
print(f"  -> shortfall: {x0 - r[3]:.9f} m")
