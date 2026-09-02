import os
# Is the "Instant sequence must have inclusive bounds" error still live?
import duckdb
from pathlib import Path

CLI = Path(os.getenv("LAKEHOUSE_ROOT")
           or Path(__file__).resolve().parents[3])
OLD = os.environ.get("MOBILITYDUCK_EXT_BASELINE", "")
L0 = (CLI / 'data/L0/L0/year=2026/month=01/day=2026-01-15.parquet').as_posix()
REGION_M, TILE_M, SEGS = 50_000.0, 1000.0, 16

con = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
con.execute('INSTALL spatial; LOAD spatial;')
con.execute(f"LOAD '{OLD}';")
print('session TZ:', con.execute("SELECT current_setting('TimeZone')").fetchone()[0])
con.execute(f"""
    CREATE OR REPLACE TEMP VIEW base_segments AS
    SELECT * EXCLUDE (traj_wkb, geometry, bbox),
           bbox.xmin AS bbox_min_x, bbox.ymin AS bbox_min_y,
           bbox.xmax AS bbox_max_x, bbox.ymax AS bbox_max_y,
           tgeompointFromBinary(traj_wkb) AS traj
    FROM read_parquet('{L0}', hive_partitioning=true);
""")

def attempt(label, sql):
    try:
        r = con.execute(sql).fetchall()
        print(f'  OK    {label:<58} -> {r[0] if r else None}')
        return True
    except Exception as e:
        print(f'  ERROR {label:<58} -> {type(e).__name__}: {str(e)[:110]}')
        return False

print('\n=== 1. synthetic degenerate temporal values ===')
CASES = {
    'single instant literal':
        "'Point(1 1)@2000-01-01'::tgeompoint",
    'sequence, zero spatial extent (moored)':
        "'[Point(1 1)@2000-01-01, Point(1 1)@2000-01-02]'::tgeompoint",
    'sequence, 2 identical instants in space+time is invalid; use 3 stationary':
        "'[Point(1 1)@2000-01-01, Point(1 1)@2000-01-02, Point(1 1)@2000-01-03]'::tgeompoint",
    'instant-set (discrete) value':
        "'{Point(1 1)@2000-01-01, Point(5 5)@2000-01-02}'::tgeompoint",
}
for label, lit in CASES.items():
    attempt(f'spaceSplit / {label}',
            f"SELECT count(*) FROM (SELECT {lit} AS t) x, "
            f"LATERAL spaceSplit(x.t, {TILE_M}, {TILE_M}, 1.0, ST_Point(0,0), FALSE) sp")
    attempt(f'splitEachNStboxes / {label}',
            f"SELECT count(*) FROM (SELECT {lit} AS t) x, "
            f"LATERAL UNNEST(splitEachNStboxes(x.t, {SEGS})) mb(box)")

print('\n=== 2. real zero-extent segments from the day (the guard target) ===')
n = con.execute("""
    SELECT count(*) FROM base_segments
    WHERE bbox_max_x = bbox_min_x AND bbox_max_y = bbox_min_y""").fetchone()[0]
print(f'  {n} real segments with zero spatial extent on 2026-01-15')
attempt('spaceSplit over every zero-extent segment',
        f"""SELECT count(*) FROM (
              SELECT * FROM base_segments
              WHERE bbox_max_x = bbox_min_x AND bbox_max_y = bbox_min_y) bs,
            LATERAL spaceSplit(bs.traj, {REGION_M}, {REGION_M}, 1.0,
                               ST_Point(0,0), FALSE) sp(spaceBin, tpoint)""")
attempt('splitEachNStboxes over every zero-extent segment',
        f"""SELECT count(*) FROM (
              SELECT * FROM base_segments
              WHERE bbox_max_x = bbox_min_x AND bbox_max_y = bbox_min_y) bs,
            LATERAL UNNEST(splitEachNStboxes(bs.traj, {SEGS})) mb(box)""")

print('\n=== 3. the enumerate_region_cells query with its guards REMOVED ===')
attempt('spaceSplit over ALL segments, no guards, no numInstants filter',
        f"""SELECT count(DISTINCT (floor(ST_X(sp.spaceBin)/{REGION_M})::INT,
                                   floor(ST_Y(sp.spaceBin)/{REGION_M})::INT))
            FROM base_segments bs,
                 LATERAL spaceSplit(bs.traj, {REGION_M}, {REGION_M}, 1.0,
                                    ST_Point(0,0), FALSE) sp(spaceBin, tpoint)""")
attempt('same, in-motion only, no guards (shipped guards would apply here)',
        f"""SELECT count(*) FROM (
              SELECT * FROM base_segments WHERE segment_type = 'in motion') bs,
            LATERAL spaceSplit(bs.traj, {REGION_M}, {REGION_M}, 1.0,
                               ST_Point(0,0), FALSE) sp(spaceBin, tpoint)""")

print('\n=== 4. full L2 spaceSplit tiling, no guards at all (1 km tiles) ===')
attempt('spaceSplit 1km over ALL segments, no filters',
        f"""SELECT count(*), sum(numInstants(sp.tpoint))
            FROM base_segments bs,
                 LATERAL spaceSplit(bs.traj, {TILE_M}, {TILE_M}, 1.0,
                                    ST_Point(0,0), FALSE) sp(spaceBin, tpoint)""")
attempt('...and how many 1-instant tiles does it emit?',
        f"""SELECT sum((numInstants(sp.tpoint)=1)::INT),
                   sum((numInstants(sp.tpoint)=2)::INT), count(*)
            FROM base_segments bs,
                 LATERAL spaceSplit(bs.traj, {TILE_M}, {TILE_M}, 1.0,
                                    ST_Point(0,0), FALSE) sp(spaceBin, tpoint)""")
