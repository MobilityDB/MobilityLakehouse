# Catalog-layer properties: Iceberg REST vs DuckLake (file- and Postgres-backed)
from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time

import duckdb
import pandas as pd
from lakehouse.query.registry import MOBILITYDUCK_EXT  # noqa: E402

SCRATCH = os.getenv(
    "DL_SCRATCH",
    os.getenv("LAKEHOUSE_SCRATCH", tempfile.gettempdir()),
)
DL_FILE = f"{SCRATCH}/dl_meta.ducklake"        # file-backed catalog (L1s, L2s, L3s)
DL_CLEAN = f"{SCRATCH}/dl_clean.ducklake"      # single-table catalog, for footprint only
PG_CAT = "dbname=ducklake_cat host=localhost"
DATA_PATH = "s3://warehouse/trips/layout_compact/L3s/"
S3_PREFIX = "s3://warehouse/trips/layout_compact"
REPS = 9
TABLES = [("L1s", 16), ("L3s", 299), ("L2s", 302)]
PRED = ("WHERE xmin<=654100 AND xmax>=640730 "
        "AND ymin<=6058230 AND ymax>=6042487")

SCHEMA_BASE = ("mmsi UBIGINT, ship_type VARCHAR, segment_type VARCHAR, traj BLOB, "
               "tmin TIMESTAMPTZ, tmax TIMESTAMPTZ, xmin DOUBLE, xmax DOUBLE, "
               "ymin DOUBLE, ymax DOUBLE, dt DATE, year BIGINT, month VARCHAR")
PARTS = {"L1s": "shard BIGINT",
         "L2s": "region_x BIGINT, region_y BIGINT",
         "L3s": "region_x BIGINT, region_y BIGINT"}

def conn(kind: str) -> tuple[duckdb.DuckDBPyConnection, str]:
    c = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    for x in ("ducklake", "httpfs", "spatial", "iceberg", "postgres"):
        c.execute(f"INSTALL {x}; LOAD {x};")
    c.execute(f"LOAD '{MOBILITYDUCK_EXT}';")
    ep = os.getenv("ICEBERG_CATALOG_PROP__S3__ENDPOINT",
                   "http://localhost:9000").removeprefix("http://")
    c.execute(f"CREATE SECRET (TYPE S3, KEY_ID '{os.getenv('AWS_ACCESS_KEY_ID','admin')}', "
              f"SECRET '{os.getenv('AWS_SECRET_ACCESS_KEY','password')}', ENDPOINT '{ep}', "
              f"URL_STYLE 'path', USE_SSL false, REGION 'us-east-1');")
    if kind == "iceberg":
        rest = os.getenv("ICEBERG_REST_URI", "http://localhost:8181")
        c.execute(f"ATTACH '' AS d (TYPE ICEBERG, ENDPOINT '{rest}', "
                  f"AUTHORIZATION_TYPE 'none');")
        return c, "d.ais."
    if kind == "ducklake_pg":
        c.execute(f"ATTACH 'ducklake:postgres:{PG_CAT}' AS d (DATA_PATH '{DATA_PATH}');")
    else:
        c.execute(f"ATTACH 'ducklake:{DL_FILE}' AS d (DATA_PATH '{DATA_PATH}');")
    return c, "d."

def planning() -> pd.DataFrame:
    rows = []
    for tbl, nfiles in TABLES:
        for kind in ("iceberg", "ducklake_file", "ducklake_pg"):
            if kind == "ducklake_pg" and tbl != "L3s":
                continue                      # only L3s is registered in the PG catalog
            lat = []
            for _ in range(REPS):
                c, pre = conn(kind)
                t = time.perf_counter()
                c.execute(f"EXPLAIN SELECT count(*) FROM {pre}{tbl} {PRED}").fetchall()
                lat.append(1000 * (time.perf_counter() - t))
                c.close()
            rows.append({"backend": kind, "table": tbl, "files": nfiles,
                         "plan_ms_median": round(statistics.median(lat), 2),
                         "plan_ms_min": round(min(lat), 2),
                         "plan_ms_max": round(max(lat), 2), "reps": REPS})
            print(f"  {kind:14} {tbl:4} ({nfiles:3} files)  "
                  f"{statistics.median(lat):6.2f} ms", flush=True)
    return pd.DataFrame(rows)

def concurrency() -> pd.DataFrame:
    rows = []
    for kind in ("iceberg", "ducklake_file", "ducklake_pg"):
        c1 = None
        try:
            c1, pre = conn(kind)
            n1 = c1.execute(f"SELECT count(*) FROM {pre}L3s").fetchone()[0]
            c2, pre2 = conn(kind)             # second reader while the first is open
            n2 = c2.execute(f"SELECT count(*) FROM {pre2}L3s").fetchone()[0]
            c2.close()
            ok, detail = True, f"{n1:,} / {n2:,}"
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}"
        finally:
            if c1 is not None:
                try:
                    c1.close()
                except Exception:
                    pass
        rows.append({"backend": kind, "two_readers_ok": ok, "detail": detail})
        print(f"  {kind:14} concurrent readers: {'OK' if ok else 'FAILED'}  {detail}",
              flush=True)
    return pd.DataFrame(rows)

def footprint() -> pd.DataFrame:
    if os.path.exists(DL_CLEAN):
        os.remove(DL_CLEAN)
    c = duckdb.connect()
    for x in ("ducklake", "httpfs"):
        c.execute(f"INSTALL {x}; LOAD {x};")
    c.execute("CREATE SECRET (TYPE S3, KEY_ID 'admin', SECRET 'password', "
              "ENDPOINT 'localhost:9000', URL_STYLE 'path', USE_SSL false, REGION 'us-east-1');")
    c.execute(f"ATTACH 'ducklake:{DL_CLEAN}' AS d (DATA_PATH '{DATA_PATH}');")
    c.execute(f"CREATE TABLE d.L3s({SCHEMA_BASE}, {PARTS['L3s']})")
    t = time.perf_counter()
    c.execute(f"""CALL ducklake_add_data_files('d','L3s',
        '{S3_PREFIX}/L3s/**/*.parquet',
        hive_partitioning => true, ignore_extra_columns => true, allow_missing => true)""")
    commit_s = time.perf_counter() - t
    c.execute("CHECKPOINT")
    c.close()
    dl_bytes = os.path.getsize(DL_CLEAN)

    # Iceberg metadata lives under the table location in MinIO
    ice_bytes = None
    try:
        import subprocess
        out = subprocess.run(["docker", "exec", "ais-minio", "du", "-sb",
                              "/data/warehouse/ais/L3s"], capture_output=True, text=True)
        ice_bytes = int(out.stdout.split()[0])
    except Exception as e:
        print(f"  iceberg metadata size unavailable: {type(e).__name__}: {e}")

    rows = [{"backend": "iceberg", "metadata_bytes": ice_bytes, "commit_s": None},
            {"backend": "ducklake_file", "metadata_bytes": dl_bytes,
             "commit_s": round(commit_s, 2)}]
    for r in rows:
        mb = f"{r['metadata_bytes']/1e6:.2f} MB" if r["metadata_bytes"] else "?"
        print(f"  {r['backend']:14} metadata {mb}", flush=True)
    return pd.DataFrame(rows)

def main() -> None:
    print("\n== planning latency (cold connection, no warm-up VIEW) ==")
    p = planning()
    print("\n== concurrency ==")
    c = concurrency()
    print("\n== metadata footprint + commit cost ==")
    f = footprint()
    p.to_csv("results/catalog_planning.csv", index=False)
    c.to_csv("results/catalog_concurrency.csv", index=False)
    f.to_csv("results/catalog_footprint.csv", index=False)
    print("\nwrote results/catalog_planning.csv, catalog_concurrency.csv, catalog_footprint.csv")

if __name__ == "__main__":
    main()
