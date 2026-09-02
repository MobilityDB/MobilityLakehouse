#!/usr/bin/env python3
# Compute-only regime analysis (no matplotlib). Prints JSON to stdout and
import json, os, sys
import duckdb

L0_GLOB = "data/trips/L0/L0/**/*.parquet"
TILE_M = 50_000
QUERY_BOX_M = 10_000

CORRIDOR_REGIONS = {
    "rodby (port)":      (651135.0, 6058230.0, 651422.0, 6058548.0),
    "puttgarden (port)": (644339.0, 6042108.0, 644896.0, 6042487.0),
    "goteborg (port)":   (666538.0, 6392057.0, 679171.0, 6403745.0),
    "belt (corridor)":   (640730.0, 6042487.0, 654100.0, 6058230.0),
}
OPEN_WATER_REGIONS = {
    "north_sea_west":  (242631.9, 6183191.2, 256001.9, 6198934.2),
    "north_sea_south": (266695.5, 6036920.7, 280065.5, 6052663.7),
    "skagerrak_open":  (510998.3, 6432011.4, 524368.3, 6447754.4),
    "kattegat_open":   (628377.8, 6267154.9, 641747.8, 6282897.9),
}

con = duckdb.connect(); con.execute("PRAGMA threads=4")
con.execute(f"""CREATE TEMP TABLE bounds AS
  SELECT octet_length(traj) AS w, xmin,xmax,ymin,ymax,
         (xmin+xmax)/2.0 AS cx0, (ymin+ymax)/2.0 AS cy0
  FROM read_parquet('{L0_GLOB}') WHERE xmin IS NOT NULL""")
N_SEG, W_SEG = con.execute("SELECT count(*), sum(w) FROM bounds").fetchone()
con.execute(f"""CREATE TEMP TABLE incid AS
  SELECT w, gx.cx AS cx, gy.cy AS cy FROM bounds,
    LATERAL UNNEST(generate_series(CAST(floor(xmin/{TILE_M}) AS BIGINT),CAST(floor(xmax/{TILE_M}) AS BIGINT))) AS gx(cx),
    LATERAL UNNEST(generate_series(CAST(floor(ymin/{TILE_M}) AS BIGINT),CAST(floor(ymax/{TILE_M}) AS BIGINT))) AS gy(cy)""")
W_INC = con.execute("SELECT sum(w) FROM incid").fetchone()[0]

def cand(x0,y0,x1,y1):
    cx0,cx1=int(x0//TILE_M),int(x1//TILE_M); cy0,cy1=int(y0//TILE_M),int(y1//TILE_M)
    tw,tc=con.execute(f"SELECT COALESCE(sum(w),0),count(DISTINCT (cx,cy)) FROM incid WHERE cx BETWEEN {cx0} AND {cx1} AND cy BETWEEN {cy0} AND {cy1}").fetchone()
    return 100.0*tw/W_INC, int(tc)
def dens(x0,y0,x1,y1):
    n,b=con.execute(f"SELECT count(*),COALESCE(sum(w),0) FROM bounds WHERE cx0 BETWEEN {x0} AND {x1} AND cy0 BETWEEN {y0} AND {y1}").fetchone()
    return int(n),int(b)

named=[]
for name,(x0,y0,x1,y1) in CORRIDOR_REGIONS.items():
    s,tc=cand(x0,y0,x1,y1); n,_=dens(x0,y0,x1,y1)
    named.append({"region":name,"kind":"corridor","area_km2":round((x1-x0)*(y1-y0)/1e6,3),
                  "local_segments":n,"candidate_share_pct":round(s,4),"tiles":tc})
for name,(x0,y0,x1,y1) in OPEN_WATER_REGIONS.items():
    s,tc=cand(x0,y0,x1,y1); n,_=dens(x0,y0,x1,y1)
    named.append({"region":name,"kind":"open_water","area_km2":round((x1-x0)*(y1-y0)/1e6,3),
                  "local_segments":n,"candidate_share_pct":round(s,4),"tiles":tc})

# density-vs-share over every non-empty tile, vectorized:
#   candidate share of a small query box in cell (cx,cy) == bytes stored in that
#   cell / W_INC  (the box is smaller than a tile, so it touches exactly one).
#   density == segments whose centroid falls in the cell.
cellbytes=dict(((int(a),int(b)),int(c)) for a,b,c in con.execute(
    "SELECT cx,cy,sum(w) FROM incid GROUP BY cx,cy").fetchall())
celldens=con.execute(
    f"SELECT CAST(floor(cx0/{TILE_M}) AS BIGINT) gx, CAST(floor(cy0/{TILE_M}) AS BIGINT) gy, count(*) FROM bounds GROUP BY 1,2").fetchall()
scatter=[]
for gx,gy,n in celldens:
    if n==0: continue
    wb=cellbytes.get((int(gx),int(gy)),0)
    scatter.append({"cx":(gx+0.5)*TILE_M,"cy":(gy+0.5)*TILE_M,"d":int(n),
                    "share":round(100.0*wb/W_INC,4)})
scatter.sort(key=lambda r:r["d"])
m=len(scatter); dec=[]
for d in range(10):
    ch=scatter[d*m//10:(d+1)*m//10]
    if ch: dec.append({"decile":d+1,"dmin":ch[0]["d"],"dmax":ch[-1]["d"],
                       "mean_share":round(sum(r["share"] for r in ch)/len(ch),4),"n":len(ch)})

out={"n_seg":N_SEG,"w_seg":W_SEG,"w_inc":W_INC,"repl":round(W_INC/W_SEG,3),
     "tile_m":TILE_M,"query_box_m":QUERY_BOX_M,"named":named,"deciles":dec,
     "n_tiles":m}
print("REGIME_JSON_START")
print(json.dumps(out))
print("REGIME_JSON_END")

# coarse 2D density histogram (UTM) for the map: 120x120 bins over data extent (best-effort)
try:
    ext=con.execute("SELECT min(cx0),max(cx0),min(cy0),max(cy0) FROM bounds").fetchone()
    NB=120; ex0,ex1,ey0,ey1=[float(v) for v in ext]
    hist=con.execute(f"""
      SELECT CAST(floor((cx0-{ex0})/(({ex1}-{ex0})/{NB})) AS INTEGER) AS bx,
             CAST(floor((cy0-{ey0})/(({ey1}-{ey0})/{NB})) AS INTEGER) AS byb,
             sum(w) AS wsum FROM bounds GROUP BY bx, byb""").fetchall()
    json.dump({"extent":[ex0,ex1,ey0,ey1],"nb":NB,
               "bins":[[int(a),int(b),int(c)] for a,b,c in hist]}, open("regime_hist.json","w"))
    print("HIST_OK")
except Exception as e:
    print("HIST_FAIL", e)
