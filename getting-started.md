<!--
Copyright(c) MobilityDB Contributors

This documentation is licensed under a
Creative Commons Attribution-Share Alike 3.0 License
https://creativecommons.org/licenses/by-sa/3.0/
-->

# Getting started with the MobilityLakehouse

This guide builds a small mobility lakehouse end to end: ingest raw events,
write TemporalParquet shards, and read the same data from another engine. It
uses MobilityDuck (DuckDB); the same files are read by MobilityDB and
MobilitySpark.

## 1. Ingest raw events and build typed trajectories

```sql
-- Load raw events, deduplicate and validate
CREATE OR REPLACE TABLE raw AS
SELECT CAST(ts_str AS TIMESTAMPTZ) AS ts,
       CAST(entity_id AS BIGINT)   AS entity_id,
       CAST(lat AS DOUBLE)         AS lat,
       CAST(lon AS DOUBLE)         AS lon
FROM read_csv_auto('events_*.csv', header = true, nullstr = '')
WHERE TRY_CAST(lat AS DOUBLE) BETWEEN  -90 AND  90
  AND TRY_CAST(lon AS DOUBLE) BETWEEN -180 AND 180
QUALIFY ROW_NUMBER() OVER (PARTITION BY CAST(entity_id AS BIGINT), ts_str
                           ORDER BY ts_str) = 1;

-- Build typed temporal sequences (one trajectory per entity)
CREATE OR REPLACE TABLE trajectories AS
SELECT entity_id,
       tgeogpointSeq(list(TGEOGPOINT(ST_Point(lon, lat), ts) ORDER BY ts)) AS traj
FROM raw
GROUP BY entity_id
HAVING count(*) >= 3;
```

## 2. Write a TemporalParquet shard with covering columns

The temporal value is stored as a lossless `BYTE_ARRAY` (MEOS-WKB); covering
columns are materialised alongside it so the lakehouse prunes files and row
groups before reading any value.

```sql
COPY (
  SELECT entity_id,
         asBinary(traj)    AS traj,                  -- canonical value (BLOB)
         Xmin(stbox(traj)) AS xmin, Xmax(stbox(traj)) AS xmax,
         Ymin(stbox(traj)) AS ymin, Ymax(stbox(traj)) AS ymax,
         Tmin(stbox(traj)) AS tmin, Tmax(stbox(traj)) AS tmax,
         SRID(traj)        AS srid,
         numInstants(traj) AS ping_count
  FROM trajectories
) TO 'lake/year=2026/month=02/day=26/shard_000.parquet'
  (FORMAT PARQUET, ROW_GROUP_SIZE 1000);
```

Annotate the file with the self-describing `temporal` footer key:

```bash
python3 tools/temporal_parquet.py annotate \
  lake/year=2026/month=02/day=26/shard_000.parquet \
  --column "name=traj,base_type=tgeogpoint,subtype=Sequence,interp=linear,srid=4326,geodetic=true"
```

## 3. Organise shards as a partition tree

```
lake/
  year=YYYY/month=MM/day=DD/shard_NNN.parquet
```

| Use case | Partition key |
| --- | --- |
| Time-series (default) | `year` / `month` / `day` |
| Spatial coverage (WGS-84) | H3 cell (`th3index`) |
| Spatial coverage (projected) | MEOS space-time tile (`spaceTimeSplit`) |
| Entity range | entity ID prefix / hash bucket |

Partitions may be nested, e.g. `year=2026/month=02/h3cell=832830fffffffff/`.

## 4. Read the same data — pruned by space and time

```sql
SELECT entity_id, asText(tgeompointFromBinary(traj))
FROM read_parquet('lake/**/*.parquet')
WHERE tmax >= TIMESTAMPTZ '2026-02-26 00:00:00+00'   -- time pruning
  AND tmin <  TIMESTAMPTZ '2026-02-27 00:00:00+00'
  AND xmax >= 4.0 AND xmin <= 5.0                     -- space pruning
  AND ymax >= 51.0 AND ymin <= 52.0;
```

The scalar predicates on the covering columns let the engine skip files and
row groups whose bounds do not intersect the query, with no spatial-aware
engine and no spatial extension.

## 5. Verify the cross-engine round-trip

A file written by one engine is read losslessly by another:

```python
# DuckDB / MobilityDuck — reconstruct from MEOS-WKB, no value conversion
import duckdb
duckdb.sql("""
  SELECT entity_id, asText(tgeompointFromBinary(traj))
  FROM read_parquet('lake/**/*.parquet')
""").show()
```

```python
# Any Parquet tool sees the value column as opaque BYTE_ARRAY
import pyarrow.parquet as pq
print(pq.read_table('lake/year=2026/month=02/day=26/shard_000.parquet').schema)
# traj is BYTE_ARRAY (MEOS-WKB); covering columns are DOUBLE / TIMESTAMP / INT
```

## 6. Promote shards to an Iceberg table

Registering the shard tree as an Apache Iceberg table adds snapshots, schema
evolution, time travel, and a REST catalog over the same files. The covering
columns become Iceberg column statistics, so the catalog prunes whole files at
the manifest level before a query reads them. This is the step the
[AIS Iceberg Explorer](https://ais-explorer-833836401560.europe-west1.run.app/)
runs over AIS vessel trajectories.
