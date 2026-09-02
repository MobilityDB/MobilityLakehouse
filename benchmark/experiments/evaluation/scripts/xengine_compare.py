# Cross-engine equivalence: every benchmark query on the SAME open L0 Parquet,
import os, glob, csv, sys
CLI    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CLI)
import duckdb, psycopg2
from lakehouse.query.registry import MOBILITYDUCK_EXT

L0DIR  = os.path.join(CLI, "data/trips/L0/L0")
GLOB   = f"{L0DIR}/year=2026/month=01/day=2026-01-1[56].parquet"
FILES  = sorted(glob.glob(GLOB))
PGCONN = "dbname=xengine_test"
TZ     = "Europe/Copenhagen"

# day window (matches -m lakehouse.query.run_iceberg WINDOWS['day'])
T0, T1, TMID = "2026-01-15 08:00:00", "2026-01-16 08:00:00", "2026-01-15 20:00:00"
D0, D1 = "2026-01-15", "2026-01-16"
BELT = "640730.0, 6042487.0, 654100.0, 6058230.0"

# ------------------------------------------------------------------ DuckDB side
def duck_conn():
    c = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    c.execute(f"SET TimeZone='{TZ}'")
    c.execute("INSTALL spatial; LOAD spatial;")
    c.execute(f"LOAD '{MOBILITYDUCK_EXT}';")
    c.execute(f"SET VARIABLE trips_glob = '{GLOB}'")
    c.execute(f"SET VARIABLE t0   = TIMESTAMP '{T0}'")
    c.execute(f"SET VARIABLE t1   = TIMESTAMP '{T1}'")
    c.execute(f"SET VARIABLE tmid = TIMESTAMP '{TMID}'")
    c.execute(f"SET VARIABLE d0   = DATE '{D0}'")
    c.execute(f"SET VARIABLE d1   = DATE '{D1}'")
    return c

def duck_run(c, sql):
    res = None
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        res = c.execute(stmt)
    return res.fetchall()

def duck_scalar(c, qname):
    sql = open(os.path.join(CLI, "queries/parquet", f"{qname}.sql")).read()
    return duck_run(c, sql)[0][0]

# ----------------------------------------------------------------- MobilityDB side
def pg_conn():
    pg = psycopg2.connect(PGCONN); pg.autocommit = True
    cur = pg.cursor(); cur.execute(f"SET TimeZone='{TZ}';")
    for ext in ("postgis", "mobilitydb CASCADE", "pg_parquet"):
        cur.execute(f"CREATE EXTENSION IF NOT EXISTS {ext};")
    cur.execute("DROP TABLE IF EXISTS l0_pq;")
    # xmin/xmax collide with PostgreSQL MVCC system columns -> renamed bxmin/bxmax
    cur.execute("""CREATE TABLE l0_pq(mmsi bigint, ship_type text, segment_type text,
       traj bytea, tmin timestamptz, tmax timestamptz,
       bxmin float8, bxmax float8, ymin float8, ymax float8, dt date);""")
    for f in FILES:
        cur.execute(f"COPY l0_pq FROM '{f}' (FORMAT parquet);")
    return pg, cur

def pg_scalar(cur, sql):
    cur.execute(sql); return cur.fetchone()[0]

# clipped CTE shared by the belt queries (bbox pre-filter + exact atStbox)
CLIPPED = f"""
  WITH cand AS (
    SELECT mmsi, ship_type, tgeompointFromEWKB(traj) AS traj FROM l0_pq
    WHERE dt BETWEEN DATE '{D0}' AND DATE '{D1}'
      AND tmax >= TIMESTAMP '{T0}' AND tmin <= TIMESTAMP '{T1}'
      AND bxmax >= 640730.0 AND bxmin <= 654100.0
      AND ymax  >= 6042487.0 AND ymin  <= 6058230.0),
  clipped AS (
    SELECT mmsi, ship_type, g FROM (
      SELECT mmsi, ship_type,
        atStbox(traj, stbox(ST_MakeEnvelope({BELT}), tstzspan('[{T0}, {T1})'))) g
      FROM cand) s WHERE g IS NOT NULL)"""

def prox(gate, margin, agg):
    return f"""{CLIPPED},
  ext AS (SELECT mmsi, g, startTimestamp(g) ts0, endTimestamp(g) ts1,
                 ST_XMin(e) x0, ST_XMax(e) x1, ST_YMin(e) y0, ST_YMax(e) y1
          FROM (SELECT mmsi, g, ST_Envelope(trajectory(g)) e FROM clipped) s WHERE e IS NOT NULL),
  cand2 AS (SELECT a.mmsi m1, b.mmsi m2, a.g t1, b.g t2 FROM ext a JOIN ext b
            ON a.mmsi < b.mmsi AND a.ts0 < b.ts1 AND a.ts1 > b.ts0
            AND a.x0 <= b.x1 + {margin} AND b.x0 <= a.x1 + {margin}
            AND a.y0 <= b.y1 + {margin} AND b.y0 <= a.y1 + {margin})
  {agg}"""

PG_SQL = {
 "clip_to_region": f"{CLIPPED} SELECT count(DISTINCT mmsi) FROM clipped;",
 "harbour_entry": f"""
   WITH cand AS (
     SELECT mmsi, atTime(tgeompointFromEWKB(traj), tstzspan('[{T0}, {T1})')) trip
     FROM l0_pq
     WHERE bxmin <= 679171.0 AND bxmax >= 666538.0
       AND ymin <= 6403745.0 AND ymax >= 6392057.0
       AND tmin < TIMESTAMP '{T1}' AND tmax > TIMESTAMP '{T0}'
       AND dt BETWEEN DATE '{D0}' AND DATE '{D1}')
   SELECT count(DISTINCT mmsi) FROM cand
   WHERE trip IS NOT NULL AND eIntersects(trip, ST_MakeEnvelope(666538.0,6392057.0,679171.0,6403745.0));""",
 "both_ports": f"""
   WITH rodby AS (SELECT DISTINCT mmsi FROM l0_pq
       WHERE bxmin<=651422.0 AND bxmax>=651135.0 AND ymin<=6058548.0 AND ymax>=6058230.0
         AND tmin<TIMESTAMP '{T1}' AND tmax>TIMESTAMP '{T0}' AND dt BETWEEN DATE '{D0}' AND DATE '{D1}'
         AND eIntersects(tgeompointFromEWKB(traj), ST_MakeEnvelope(651135.0,6058230.0,651422.0,6058548.0))),
        putt AS (SELECT DISTINCT mmsi FROM l0_pq
       WHERE bxmin<=644896.0 AND bxmax>=644339.0 AND ymin<=6042487.0 AND ymax>=6042108.0
         AND tmin<TIMESTAMP '{T1}' AND tmax>TIMESTAMP '{T0}' AND dt BETWEEN DATE '{D0}' AND DATE '{D1}'
         AND eIntersects(tgeompointFromEWKB(traj), ST_MakeEnvelope(644339.0,6042108.0,644896.0,6042487.0)))
   SELECT count(*) FROM rodby JOIN putt USING(mmsi);""",
 "position_interpolation": f"""{CLIPPED}
   SELECT count(DISTINCT mmsi) FROM (
     SELECT mmsi, valueAtTimestamp(g, TIMESTAMP '{TMID}') p FROM clipped) s WHERE p IS NOT NULL;""",
 "collision":      prox(300, 300, "SELECT count(*) FROM (SELECT DISTINCT m1,m2 FROM cand2 WHERE nearestApproachDistance(t1,t2) < 300) s;"),
 "encounter_zone": prox(500, 500, "SELECT count(*) FROM (SELECT DISTINCT m1,m2 FROM cand2 WHERE nearestApproachDistance(t1,t2) < 500) s;"),
 "nearest_approach": prox(0, 2000, "SELECT round(min(nearestApproachDistance(t1,t2))) FROM cand2;"),
 "fleet_summary":  f"{CLIPPED} SELECT round((sum(length(g))/1000.0)::numeric, 1) FROM clipped;",
 "bounding_box":   f"""{CLIPPED}
   SELECT round(avg((x1-x0)*(y1-y0)/1e6)::numeric, 2) FROM (
     SELECT mmsi, min(x0) x0, max(x1) x1, min(y0) y0, max(y1) y1 FROM (
       SELECT mmsi, ST_XMin(trajectory(g)) x0, ST_XMax(trajectory(g)) x1,
              ST_YMin(trajectory(g)) y0, ST_YMax(trajectory(g)) y1 FROM clipped) s
     GROUP BY mmsi) t;""",
 "speed_profile":  f"""{CLIPPED}
   SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY vmax) * 1.94384)::numeric, 1) FROM (
     SELECT mmsi, max(maxValue(speed(g))) vmax FROM clipped GROUP BY mmsi) s;""",
}

# vessel-set queries: also compare the actual mmsi sets (symmetric difference)
_belt_clip = (f"atStbox(tgeompointFromEWKB(traj), stbox(ST_MakeEnvelope({BELT}), "
              f"span(TIMESTAMP '{T0}', TIMESTAMP '{T1}', true, false)))")
_belt_where = (f"WHERE dt BETWEEN DATE '{D0}' AND DATE '{D1}' AND tmax>=TIMESTAMP '{T0}' "
               f"AND tmin<=TIMESTAMP '{T1}' AND xmax>=640730.0 AND xmin<=654100.0 "
               f"AND ymax>=6042487.0 AND ymin<=6058230.0")
DUCK_SET = {
 "clip_to_region": f"SELECT DISTINCT mmsi FROM (SELECT mmsi, {_belt_clip} g FROM read_parquet('{GLOB}') {_belt_where}) WHERE g IS NOT NULL;",
 "harbour_entry": f"""SELECT DISTINCT mmsi FROM (
     SELECT mmsi, atTime(tgeompointFromEWKB(traj), span(TIMESTAMP '{T0}', TIMESTAMP '{T1}', true, false)) trip
     FROM read_parquet('{GLOB}')
     WHERE xmin<=679171.0 AND xmax>=666538.0 AND ymin<=6403745.0 AND ymax>=6392057.0
       AND tmin<TIMESTAMP '{T1}' AND tmax>TIMESTAMP '{T0}' AND dt BETWEEN DATE '{D0}' AND DATE '{D1}')
     WHERE trip IS NOT NULL AND eIntersects(trip, ST_MakeEnvelope(666538.0,6392057.0,679171.0,6403745.0));""",
 "position_interpolation": f"SELECT DISTINCT mmsi FROM (SELECT mmsi, valueAtTimestamp({_belt_clip}, TIMESTAMP '{TMID}') p FROM read_parquet('{GLOB}') {_belt_where}) WHERE p IS NOT NULL;",
 "both_ports": f"""
   WITH rodby AS (SELECT DISTINCT mmsi FROM read_parquet('{GLOB}')
       WHERE xmin<=651422.0 AND xmax>=651135.0 AND ymin<=6058548.0 AND ymax>=6058230.0
         AND tmin<TIMESTAMP '{T1}' AND tmax>TIMESTAMP '{T0}' AND dt BETWEEN DATE '{D0}' AND DATE '{D1}'
         AND eIntersects(tgeompointFromEWKB(traj), ST_MakeEnvelope(651135.0,6058230.0,651422.0,6058548.0))),
        putt AS (SELECT DISTINCT mmsi FROM read_parquet('{GLOB}')
       WHERE xmin<=644896.0 AND xmax>=644339.0 AND ymin<=6042487.0 AND ymax>=6042108.0
         AND tmin<TIMESTAMP '{T1}' AND tmax>TIMESTAMP '{T0}' AND dt BETWEEN DATE '{D0}' AND DATE '{D1}'
         AND eIntersects(tgeompointFromEWKB(traj), ST_MakeEnvelope(644339.0,6042108.0,644896.0,6042487.0)))
   SELECT mmsi FROM rodby JOIN putt USING(mmsi);""",
}
PG_SET = {
 "clip_to_region": f"{CLIPPED} SELECT array_agg(DISTINCT mmsi) FROM clipped;",
 "harbour_entry": f"""
   WITH cand AS (
     SELECT mmsi, atTime(tgeompointFromEWKB(traj), tstzspan('[{T0}, {T1})')) trip FROM l0_pq
     WHERE bxmin <= 679171.0 AND bxmax >= 666538.0 AND ymin <= 6403745.0 AND ymax >= 6392057.0
       AND tmin < TIMESTAMP '{T1}' AND tmax > TIMESTAMP '{T0}' AND dt BETWEEN DATE '{D0}' AND DATE '{D1}')
   SELECT array_agg(DISTINCT mmsi) FROM cand
   WHERE trip IS NOT NULL AND eIntersects(trip, ST_MakeEnvelope(666538.0,6392057.0,679171.0,6403745.0));""",
 "position_interpolation": f"""{CLIPPED}
   SELECT array_agg(DISTINCT mmsi) FROM (
     SELECT mmsi, valueAtTimestamp(g, TIMESTAMP '{TMID}') p FROM clipped) s WHERE p IS NOT NULL;""",
 "both_ports": f"""
   WITH rodby AS (SELECT DISTINCT mmsi FROM l0_pq
       WHERE bxmin<=651422.0 AND bxmax>=651135.0 AND ymin<=6058548.0 AND ymax>=6058230.0
         AND tmin<TIMESTAMP '{T1}' AND tmax>TIMESTAMP '{T0}' AND dt BETWEEN DATE '{D0}' AND DATE '{D1}'
         AND eIntersects(tgeompointFromEWKB(traj), ST_MakeEnvelope(651135.0,6058230.0,651422.0,6058548.0))),
        putt AS (SELECT DISTINCT mmsi FROM l0_pq
       WHERE bxmin<=644896.0 AND bxmax>=644339.0 AND ymin<=6042487.0 AND ymax>=6042108.0
         AND tmin<TIMESTAMP '{T1}' AND tmax>TIMESTAMP '{T0}' AND dt BETWEEN DATE '{D0}' AND DATE '{D1}'
         AND eIntersects(tgeompointFromEWKB(traj), ST_MakeEnvelope(644339.0,6042108.0,644896.0,6042487.0)))
   SELECT array_agg(DISTINCT mmsi) FROM rodby JOIN putt USING(mmsi);""",
}

ORDER = ["clip_to_region","harbour_entry","both_ports","position_interpolation",
         "collision","encounter_zone","nearest_approach",
         "fleet_summary","bounding_box","speed_profile"]

def num(x):
    try: return float(x)
    except (TypeError, ValueError): return x

def main():
    dc = duck_conn()
    pg, cur = pg_conn()
    cur.execute("SELECT count(*) FROM l0_pq;")
    print(f"MobilityDB loaded {cur.fetchone()[0]} L0 segments via pg_parquet "
          f"({len(FILES)} files)\n")
    rows = []
    for q in ORDER:
        try:    d = duck_scalar(dc, q)
        except Exception as e: d = f"ERR:{type(e).__name__}"
        try:    p = pg_scalar(cur, PG_SQL[q])
        except Exception as e: p = f"ERR:{str(e).splitlines()[0][:60]}"
        dn, pn = num(d), num(p)
        match = (isinstance(dn, float) and isinstance(pn, float)
                 and abs(dn - pn) <= max(0.05, 0.001*abs(dn))) or (dn == pn)
        symdiff = ""
        if q in PG_SET:
            try:
                ds = {r[0] for r in duck_run(dc, DUCK_SET[q])}
                cur.execute(PG_SET[q]); ps = set(cur.fetchone()[0] or [])
                symdiff = len(ds ^ ps)
            except Exception as e: symdiff = f"ERR:{type(e).__name__}"
        rows.append((q, d, p, "yes" if match else "NO", symdiff))
        print(f"{q:24} duck={str(d):>10}  pg={str(p):>10}  match={'yes' if match else 'NO':>3}  symdiff={symdiff}")
    cur.close(); pg.close()
    out = os.path.join(CLI, "results/xengine.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["query","mobilityduck","mobilitydb","match","symdiff"])
        w.writerows(rows)
    print(f"\nwrote {out}")

if __name__ == "__main__":
    main()
