# Warehouse data-read measurement: the index-filtering counterpart to catalog pruning
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import psycopg2

PGCONN = "dbname=mdb_warehouse"
TZ = "Europe/Copenhagen"
T0, T1 = "2026-01-15 08:00:00", "2026-01-16 08:00:00"
TMID = "2026-01-15 20:00:00"
BELT = "640730.0, 6042487.0, 654100.0, 6058230.0"
BLOCK = 8192

# The `trips` layouts store tmin/tmax +1h ahead of the timestamps inside traj, so the
# lakehouse prunes on the sidecar clock and clips on the trajectory clock. The warehouse
# dropped its sidecars, so it emulates that split to stay comparable: prune shifted back
# 1h, clip on the true window. Mirrors mobilitydb_warehouse.ipynb.
SIDECAR_SHIFT = dt.timedelta(hours=1)
_F = "%Y-%m-%d %H:%M:%S"

def _shift(ts: str) -> str:
    return (dt.datetime.strptime(ts, _F) - SIDECAR_SHIFT).strftime(_F)

P0, P1 = _shift(T0), _shift(T1)

def sbox(env: str, t0: str = T0, t1: str = T1) -> str:
    return f"stbox(ST_MakeEnvelope({env}), tstzspan('[{t0}, {t1})'))"

def pbox(env: str) -> str:
    return sbox(env, P0, P1)

BELT_SB, BELT_PB = sbox(BELT), pbox(BELT)
CLIPPED = f"""WITH clipped AS (
   SELECT mmsi, ship_type, atStbox(traj, {BELT_SB}) g FROM l0_wh
   WHERE traj && {BELT_PB}),
 c AS (SELECT mmsi, ship_type, g FROM clipped WHERE g IS NOT NULL)"""

def prox(margin: int, agg: str) -> str:
    return f"""{CLIPPED},
   ext AS (SELECT mmsi,g,startTimestamp(g) ts0,endTimestamp(g) ts1,
                  ST_XMin(e) x0,ST_XMax(e) x1,ST_YMin(e) y0,ST_YMax(e) y1
           FROM (SELECT mmsi,g,ST_Envelope(trajectory(g)) e FROM c) s WHERE e IS NOT NULL),
   cand2 AS (SELECT a.mmsi m1,b.mmsi m2,a.g t1,b.g t2 FROM ext a JOIN ext b
             ON a.mmsi<b.mmsi AND a.ts0<b.ts1 AND a.ts1>b.ts0
             AND a.x0<=b.x1+{margin} AND b.x0<=a.x1+{margin}
             AND a.y0<=b.y1+{margin} AND b.y0<=a.y1+{margin})
   {agg}"""

QUERIES = {
    "clip_to_region": f"{CLIPPED} SELECT count(DISTINCT mmsi) FROM c;",
    "harbour_entry": f"""SELECT count(DISTINCT mmsi) FROM (
        SELECT mmsi, atTime(traj, tstzspan('[{T0}, {T1})')) trip FROM l0_wh
        WHERE traj && {pbox('666538.0,6392057.0,679171.0,6403745.0')}) s
      WHERE trip IS NOT NULL
        AND eIntersects(trip, ST_MakeEnvelope(666538.0,6392057.0,679171.0,6403745.0));""",
    "both_ports": f"""WITH rodby AS (SELECT DISTINCT mmsi FROM l0_wh
          WHERE traj && {pbox('651135.0,6058230.0,651422.0,6058548.0')}
            AND eIntersects(traj, ST_MakeEnvelope(651135.0,6058230.0,651422.0,6058548.0))),
         putt AS (SELECT DISTINCT mmsi FROM l0_wh
          WHERE traj && {pbox('644339.0,6042108.0,644896.0,6042487.0')}
            AND eIntersects(traj, ST_MakeEnvelope(644339.0,6042108.0,644896.0,6042487.0)))
      SELECT count(*) FROM rodby JOIN putt USING(mmsi);""",
    "position_interpolation": f"""{CLIPPED}
      SELECT count(DISTINCT mmsi) FROM (
        SELECT mmsi, valueAtTimestamp(g, TIMESTAMP '{TMID}') p FROM c) s WHERE p IS NOT NULL;""",
    "collision": prox(300, "SELECT count(*) FROM (SELECT DISTINCT m1,m2 FROM cand2 "
                           "WHERE nearestApproachDistance(t1,t2)<300) s;"),
    "encounter_zone": prox(500, "SELECT count(*) FROM (SELECT DISTINCT m1,m2 FROM cand2 "
                                "WHERE nearestApproachDistance(t1,t2)<500) s;"),
    "nearest_approach": prox(2000, "SELECT round(min(nearestApproachDistance(t1,t2))) FROM cand2;"),
    "fleet_summary": f"{CLIPPED} SELECT round((sum(length(g))/1000.0)::numeric,1) FROM c;",
    "bounding_box": f"""{CLIPPED}
      SELECT round(avg((x1-x0)*(y1-y0)/1e6)::numeric,2) FROM (
        SELECT mmsi, min(x0) x0, max(x1) x1, min(y0) y0, max(y1) y1 FROM (
          SELECT mmsi, ST_XMin(trajectory(g)) x0, ST_XMax(trajectory(g)) x1,
                 ST_YMin(trajectory(g)) y0, ST_YMax(trajectory(g)) y1 FROM c) s GROUP BY mmsi) t;""",
    "speed_profile": f"""{CLIPPED}
      SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY vmax)*1.94384)::numeric,1) FROM (
        SELECT mmsi, max(maxValue(speed(g))) vmax FROM c GROUP BY mmsi) s;""",
}
ORDER = ["clip_to_region", "harbour_entry", "both_ports", "position_interpolation",
         "collision", "encounter_zone", "nearest_approach", "fleet_summary",
         "bounding_box", "speed_profile"]

KEYS = ("Shared Hit Blocks", "Shared Read Blocks", "Local Hit Blocks",
        "Local Read Blocks", "Temp Read Blocks")

def buffers(cur, sql: str) -> dict:
    cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql)
    plan = cur.fetchone()[0]
    if isinstance(plan, str):
        plan = json.loads(plan)
    root = plan[0]["Plan"] if isinstance(plan, list) else plan["Plan"]
    acc: dict[str, int] = dict.fromkeys(KEYS, 0)

    def walk(n):
        for k in KEYS:
            acc[k] += n.get(k, 0)
        for ch in n.get("Plans", []):
            walk(ch)

    walk(root)
    return acc

def main() -> None:
    pg = psycopg2.connect(PGCONN)
    pg.autocommit = True
    cur = pg.cursor()
    cur.execute(f"SET TimeZone='{TZ}';")
    rows = []
    for q in ORDER:
        acc = buffers(cur, QUERIES[q])
        blocks = acc["Shared Hit Blocks"] + acc["Shared Read Blocks"]
        rows.append({
            "backend": "mobilitydb_warehouse_L0",
            "query": q,
            "shared_hit_blocks": acc["Shared Hit Blocks"],
            "shared_read_blocks": acc["Shared Read Blocks"],
            "temp_read_blocks": acc["Temp Read Blocks"],
            "blocks_total": blocks,
            "bytes_read": blocks * BLOCK,
        })
        print(f"  {q:24} blocks={blocks:>8,}  {blocks*BLOCK/1e6:8.1f} MB", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv("results/warehouse_bytes.csv", index=False)
    print("\nwrote results/warehouse_bytes.csv")
    print(f"total {df.bytes_read.sum()/1e6:.0f} MB across {len(df)} queries")

if __name__ == "__main__":
    main()
