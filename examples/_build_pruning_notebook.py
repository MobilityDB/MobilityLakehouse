"""Builds examples/pruning-at-scale.ipynb.

Run: python3 examples/_build_pruning_notebook.py
Then: MOBILITYDUCK=<duckdb-with-mobilityduck> jupyter nbconvert --to notebook \
    --execute --inplace examples/pruning-at-scale.ipynb
"""
import nbformat as nbf
from pathlib import Path

nb = nbf.v4.new_notebook()
cells = []
def md(t): cells.append(nbf.v4.new_markdown_cell(t))
def code(t): cells.append(nbf.v4.new_code_cell(t))

md("""# Pruning at scale: partitions and covering columns

A lakehouse is fast because it reads as little as possible. This notebook shows
the lakehouse skipping data at the **file** level: a partitioned TemporalParquet
tree where a day-and-region query opens only the shards it must.

Three pruning levels stack:

1. **partition pruning** — skip whole shard files by partition key (shown here),
2. **row-group pruning** — skip Parquet row groups by the covering columns'
   min/max,
3. **manifest pruning** — once the tree is an Iceberg table, skip whole files at
   the catalog before reading.

Every cell runs against a real MobilityDuck engine (see the
[quickstart](https://github.com/MobilityDB/MobilityLakehouse/blob/main/examples/mobility-lakehouse-quickstart.ipynb)
for setup).""")

code("""import os, json, subprocess, pandas as pd

MOBILITYDUCK = os.environ.get("MOBILITYDUCK", "duckdb")
DB = "pruning.duckdb"

def duck(sql: str) -> pd.DataFrame:
    r = subprocess.run([MOBILITYDUCK, DB, "-json", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return pd.DataFrame(json.loads(r.stdout or "[]"))

def files_read(sql: str) -> str:
    \"\"\"Run EXPLAIN ANALYZE and return the scan's file-pruning line.\"\"\"
    r = subprocess.run([MOBILITYDUCK, DB, "-c", "EXPLAIN ANALYZE " + sql],
                       capture_output=True, text=True)
    lines = [l.strip(" │") for l in r.stdout.splitlines()
             if "Files" in l and ("Scanning" in l or "Total" in l)]
    return " · ".join(lines)""")

md("""## A partitioned lake — one shard per day

Five days of AIS pings, written to a Hive-partitioned tree (`day=YYYY-MM-DD/`),
each shard carrying the covering columns.""")

code("""duck(\"\"\"
CREATE OR REPLACE TABLE raw(mmsi BIGINT, ts TIMESTAMPTZ, lon DOUBLE, lat DOUBLE);
INSERT INTO raw VALUES
  (211512000,'2026-02-26 08:00:00+00',4.40,51.20),(211512000,'2026-02-26 10:00:00+00',4.95,51.48),
  (244660000,'2026-02-27 08:00:00+00',3.10,51.95),(244660000,'2026-02-27 10:00:00+00',3.80,52.20),
  (305000000,'2026-02-28 08:00:00+00',5.00,51.00),(305000000,'2026-02-28 10:00:00+00',5.40,51.12),
  (211512000,'2026-03-01 08:00:00+00',4.50,51.30),(211512000,'2026-03-01 10:00:00+00',4.85,51.44),
  (244660000,'2026-03-02 08:00:00+00',3.30,52.05),(244660000,'2026-03-02 10:00:00+00',3.95,52.18);
\"\"\")

duck(\"\"\"
COPY (
  SELECT (ts::DATE)::VARCHAR AS day, mmsi, asBinary(traj) AS traj,
         Xmin(stbox(traj)) xmin, Xmax(stbox(traj)) xmax,
         Ymin(stbox(traj)) ymin, Ymax(stbox(traj)) ymax,
         Tmin(stbox(traj)) tmin, Tmax(stbox(traj)) tmax
  FROM (SELECT ts::DATE AS d, mmsi,
               tgeogpointSeq(list(TGEOGPOINT(ST_Point(lon,lat), ts) ORDER BY ts)) AS traj,
               min(ts) AS ts
        FROM raw GROUP BY ts::DATE, mmsi)
) TO 'lake' (FORMAT PARQUET, PARTITION_BY (day), OVERWRITE_OR_IGNORE);
\"\"\")

import glob
sorted(p.replace("lake/", "") for p in glob.glob("lake/**/*.parquet", recursive=True))""")

md("""## Query one day — partitions prune the rest

A query for a single day reads only that day's shard. `EXPLAIN ANALYZE` reports
how many of the partition files the engine actually opened.""")

code("""q = \"\"\"SELECT mmsi FROM read_parquet('lake/**/*.parquet', hive_partitioning=true)
         WHERE day = '2026-03-01'\"\"\"
print("result:")
print(duck(q).to_string(index=False))
print("\\npruning:", files_read(q))""")

md("""Add a **spatial** predicate on the covering columns and the engine prunes
further — within the day's shard it skips row groups whose bounding box does not
meet the query box (and, once this tree is an Iceberg table, whole files at the
manifest level before any read).""")

code("""q = \"\"\"SELECT mmsi FROM read_parquet('lake/**/*.parquet', hive_partitioning=true)
         WHERE day = '2026-02-27' AND xmax >= 3.0 AND xmin <= 4.0
                                  AND ymax >= 51.5 AND ymin <= 52.5\"\"\"
print("result:")
print(duck(q).to_string(index=False))
print("\\npruning:", files_read(q))""")

md("""## What this shows

The same data, organised as a partitioned TemporalParquet tree, lets the engine
open only the shards a query needs — and the covering columns prune further
inside each shard, and at the Iceberg manifest level once the tree is a catalog
table. No spatial index server; the layout and the covering statistics do the
work.

Next: the [covering-columns](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md)
spec, the [conformance](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/conformance.md)
criteria every engine meets, and the live
[AIS Iceberg Explorer](https://ais-explorer-833836401560.europe-west1.run.app/).""")

nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
}
out = Path(__file__).parent / "pruning-at-scale.ipynb"
nbf.write(nb, str(out))
print("wrote", out, "with", len(cells), "cells")
