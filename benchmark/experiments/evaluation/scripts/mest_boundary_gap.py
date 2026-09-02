import os
# Where does MEST lose 2.36% of track length?
import duckdb

OLD = os.environ.get("MOBILITYDUCK_EXT_BASELINE", "")
L0 = 'data/L0/L0/year=2026/month=01/day=2026-01-15.parquet'
REGION_M, SEGS = 50_000.0, 16

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

tot = {'kept': 0.0, 'dropped': 0.0, 'n_kept': 0, 'n_drop': 0, 'eps': 0}
CELLS = [(12, 120), (13, 120), (12, 121), (13, 121), (13, 127), (11, 122)]
for rx, ry in CELLS:
    x0, y0 = rx * REGION_M, ry * REGION_M
    x1, y1 = x0 + REGION_M, y0 + REGION_M
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE frag AS
        SELECT Xmin(mb.box) AS bx, Ymin(mb.box) AS bmin,
               length(atStbox(bs.traj, mb.box, true)) AS len
        FROM (
            SELECT * FROM (
                SELECT start_time, end_time,
                       UNNEST(sequences(atStbox(traj,
                           ST_MakeEnvelope({x0},{y0},{x1},{y1})::STBOX, true))) AS traj
                FROM base_segments
                WHERE segment_type = 'in motion'
                  AND bbox_max_x >= {x0} AND bbox_min_x < {x1}
                  AND bbox_max_y >= {y0} AND bbox_min_y < {y1}
            ) WHERE traj IS NOT NULL
        ) bs,
        LATERAL UNNEST(splitEachNStboxes(bs.traj, {SEGS})) AS mb(box)
    """)
    r = con.execute(f"""
        SELECT
          sum(CASE WHEN bx >= {x0} AND bx < {x1} AND bmin >= {y0} AND bmin < {y1}
                   THEN len ELSE 0 END) AS kept_m,
          sum(CASE WHEN NOT (bx >= {x0} AND bx < {x1} AND bmin >= {y0} AND bmin < {y1})
                   THEN len ELSE 0 END) AS drop_m,
          count(*) FILTER (WHERE bx >= {x0} AND bx < {x1} AND bmin >= {y0} AND bmin < {y1}) AS n_keep,
          count(*) FILTER (WHERE NOT (bx >= {x0} AND bx < {x1} AND bmin >= {y0} AND bmin < {y1})) AS n_drop,
          count(*) FILTER (WHERE (bx < {x0} AND bx > {x0} - 0.001)
                              OR (bmin < {y0} AND bmin > {y0} - 0.001)) AS n_eps
        FROM frag
    """).fetchone()
    kept, dropped = r[0] or 0, r[1] or 0
    pct = 100 * dropped / (kept + dropped) if (kept + dropped) else 0
    print(f"cell ({rx},{ry}): boxes kept={r[2]:<6} dropped={r[3]:<4} "
          f"| length dropped {dropped/1000:8.1f} km ({pct:5.2f}%) "
          f"| dropped within 1mm of the edge: {r[4]}")
    tot['kept'] += kept; tot['dropped'] += dropped
    tot['n_kept'] += r[2]; tot['n_drop'] += r[3]; tot['eps'] += r[4]

t = tot['kept'] + tot['dropped']
print(f"\nTOTAL over {len(CELLS)} cells: boxes {tot['n_kept']} kept / {tot['n_drop']} dropped")
print(f"  length dropped: {tot['dropped']/1000:.1f} km of {t/1000:.1f} km "
      f"({100*tot['dropped']/t:.2f}%)")
print(f"  of the dropped boxes, {tot['eps']} start within 1 mm of the cell edge "
      f"-> floating-point, not genuine out-of-cell")
