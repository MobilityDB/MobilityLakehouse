"""Builds examples/lake-analytics.ipynb.

Run: python3 examples/_build_analytics_notebook.py
Then: MOBILITYDUCK=<duckdb-with-mobilityduck> jupyter nbconvert --to notebook \
    --execute --inplace examples/lake-analytics.ipynb
"""
import nbformat as nbf
from pathlib import Path

nb = nbf.v4.new_notebook()
cells = []
def md(t): cells.append(nbf.v4.new_markdown_cell(t))
def code(t): cells.append(nbf.v4.new_code_cell(t))

md("""# Querying the lake — temporal analytics on AIS trajectories

The [quickstart](./mobility-lakehouse-quickstart.ipynb) wrote a TemporalParquet
lake and pruned it. This notebook shows **what temporal types give you that plain
points cannot** — read straight from the lake, no re-encoding:

1. per-trajectory metrics (length, average speed, duration),
2. **time travel** — where was each vessel at a given instant,
3. a temporal slice — the trajectory during a time window,
4. a spatiotemporal query — vessels in a region during a window, pruned by the
   covering columns.

Every cell runs against a real MobilityDuck engine (see the quickstart's *Setup*).""")

code("""import os, json, subprocess, pandas as pd

MOBILITYDUCK = os.environ.get("MOBILITYDUCK", "duckdb")
DB = "lake_analytics.duckdb"

def duck(sql: str) -> pd.DataFrame:
    r = subprocess.run([MOBILITYDUCK, DB, "-json", "-c", sql],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return pd.DataFrame(json.loads(r.stdout or "[]"))""")

md("""## Build a small lake

A handful of vessels, each a `tgeogpoint` trajectory, written to a TemporalParquet
shard with covering columns (see the quickstart for the details).""")

code("""duck(\"\"\"
CREATE OR REPLACE TABLE raw(mmsi BIGINT, ts TIMESTAMPTZ, lon DOUBLE, lat DOUBLE);
INSERT INTO raw VALUES
  (211512000,'2026-02-26 08:00:00+00',4.40,51.20),(211512000,'2026-02-26 08:45:00+00',4.55,51.27),
  (211512000,'2026-02-26 09:30:00+00',4.72,51.36),(211512000,'2026-02-26 10:30:00+00',4.95,51.48),
  (244660000,'2026-02-26 08:15:00+00',3.10,51.95),(244660000,'2026-02-26 09:10:00+00',3.48,52.00),
  (244660000,'2026-02-26 10:05:00+00',3.80,52.10),(244660000,'2026-02-26 11:00:00+00',4.05,52.20),
  (305000000,'2026-02-26 08:30:00+00',4.30,51.40),(305000000,'2026-02-26 09:15:00+00',4.58,51.44),
  (305000000,'2026-02-26 10:00:00+00',4.83,51.52),(305000000,'2026-02-26 10:50:00+00',5.02,51.58);
\"\"\")

duck(\"\"\"
COPY (
  SELECT mmsi, asBinary(traj) AS traj,
         Xmin(stbox(traj)) xmin, Xmax(stbox(traj)) xmax,
         Ymin(stbox(traj)) ymin, Ymax(stbox(traj)) ymax,
         Tmin(stbox(traj)) tmin, Tmax(stbox(traj)) tmax, SRID(traj) srid
  FROM (SELECT mmsi, tgeogpointSeq(list(TGEOGPOINT(ST_Point(lon,lat), ts) ORDER BY ts)) traj
        FROM raw GROUP BY mmsi)
) TO 'analytics_shard.parquet' (FORMAT PARQUET);
\"\"\")
duck("SELECT count(*) AS vessels FROM read_parquet('analytics_shard.parquet')")""")

md("""## 1. Per-trajectory metrics

`length` (geodetic → metres), `speed` (a `tfloat` → time-weighted average), and
`duration` come straight from the temporal value. None of this is expressible on a
table of isolated points.""")

code("""duck(\"\"\"
WITH t AS (SELECT mmsi, tgeogpointFromBinary(traj) AS traj
           FROM read_parquet('analytics_shard.parquet'))
SELECT mmsi,
       round(length(traj)/1000, 1)        AS length_km,
       round(twAvg(speed(traj))*3.6, 1)    AS avg_kmh,
       duration(traj)                      AS duration
FROM t ORDER BY mmsi
\"\"\")""")

md("""## 2. Time travel — where was each vessel at 09:00?

`valueAtTimestamp` evaluates the trajectory function at any instant — interpolating
between pings. This is *within-trajectory* time travel (distinct from Iceberg
snapshot time travel, which moves between table versions).""")

code("""duck(\"\"\"
WITH t AS (SELECT mmsi, tgeogpointFromBinary(traj) AS traj
           FROM read_parquet('analytics_shard.parquet'))
SELECT mmsi,
       ST_AsText(valueAtTimestamp(traj, TIMESTAMPTZ '2026-02-26 09:00:00+00')) AS position_at_0900
FROM t ORDER BY mmsi
\"\"\")""")

md("""## 3. A temporal slice

`atTime` restricts a trajectory to a time window, interpolating the endpoints —
the sub-trajectory actually travelled during the window.""")

code("""duck(\"\"\"
WITH t AS (SELECT mmsi, tgeogpointFromBinary(traj) AS traj
           FROM read_parquet('analytics_shard.parquet'))
SELECT mmsi,
       numInstants(atTime(traj, tstzspan '[2026-02-26 09:00:00+00, 2026-02-26 10:00:00+00]')) AS pings_in_window,
       round(length(atTime(traj, tstzspan '[2026-02-26 09:00:00+00, 2026-02-26 10:00:00+00]'))/1000, 1) AS km_in_window
FROM t ORDER BY mmsi
\"\"\")""")

md("""## 4. Spatiotemporal query — who was in the box during the window?

The lakehouse-native filter: the covering columns prune by bounding box and time
before any trajectory is decoded, then a temporal predicate confirms the slice is
non-empty. Here: vessels whose path's bounds meet the Scheldt-approaches box during
the 09:00–10:30 window.""")

code("""duck(\"\"\"
SELECT mmsi
FROM read_parquet('analytics_shard.parquet')
WHERE tmax >= TIMESTAMPTZ '2026-02-26 09:00:00+00'        -- temporal overlap
  AND tmin <  TIMESTAMPTZ '2026-02-26 10:30:00+00'
  AND xmax >= 4.5 AND xmin <= 5.1                          -- covering-column space prune
  AND ymax >= 51.3 AND ymin <= 51.7
ORDER BY mmsi
\"\"\")""")

md("""## What this shows

The lake stores trajectories as open, lossless TemporalParquet — yet you query
them as **functions of time**: length and speed, position at any instant, slices
over windows, and spatiotemporal filters pruned by the covering columns. The same
data, the same queries, run on MobilityDB, MobilityDuck, and MobilitySpark.

Next: the [format specification](../spec/temporalparquet.md), the
[covering-columns](../spec/covering-columns.md) mechanism, and the live
[AIS Iceberg Explorer](https://ais-explorer-833836401560.europe-west1.run.app/).""")

nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
}
out = Path(__file__).parent / "lake-analytics.ipynb"
nbf.write(nb, str(out))
print("wrote", out, "with", len(cells), "cells")
