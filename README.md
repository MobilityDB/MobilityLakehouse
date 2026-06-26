<!--
Copyright(c) MobilityDB Contributors

This documentation is licensed under a
Creative Commons Attribution-Share Alike 3.0 License
https://creativecommons.org/licenses/by-sa/3.0/
-->

# MobilityLakehouse

**An open lakehouse for moving objects.** Store and query trajectories — and,
soon, spatial and spatiotemporal rasters — as open **Apache Iceberg** tables on
object storage, readable by every engine in the MobilityDB ecosystem without
conversion.

▶ **See it live:** [AIS Iceberg Explorer](https://ais-explorer-833836401560.europe-west1.run.app/)
— AIS vessel positions as temporal trajectories, explored interactively over an
Iceberg lakehouse.

---

## About

Mobility data — ships, vehicles, people, sensors — is fundamentally *temporal*:
a trajectory is a function from time to space, not a pile of points. Mainstream
lakehouse formats have no notion of it. The MobilityLakehouse adds one, on top
of the open standards the data ecosystem already runs on.

It has two layers, with two precise roles:

- **The lake — an open file substrate.** A mobility value (a `tgeompoint`
  trajectory, a `tfloat` speed curve, …) is stored as a Parquet `BYTE_ARRAY`
  carrying its compact MEOS-WKB encoding, with a self-describing `temporal`
  footer key modelled on [GeoParquet](https://geoparquet.org/). Plain Parquet
  files: portable, engine-agnostic, no catalog required, losslessly
  round-trippable.
- **The lakehouse — a table layer.** Apache Iceberg organises those files into
  tables with ACID snapshots, schema evolution, **time travel**, and a REST
  catalog. Materialised **covering columns** (bounding box + time extent) let
  Iceberg prune whole files, and Parquet prune row groups, *before* reading any
  trajectory — fast spatial-temporal filtering with no spatial-aware engine.

The result: the same trajectory dataset is queryable from PostgreSQL, DuckDB,
or Spark, with SQL, directly on object storage.

## How it works

```
raw events (CSV, MQTT, NMEA, AIS, …)
        │   build typed sequences  →  tgeompoint / tgeogpoint / tfloat / …
        ▼
TemporalParquet shards on object storage
   value column:    BYTE_ARRAY (MEOS-WKB), lossless
   covering columns: xmin xmax ymin ymax [zmin zmax] tmin tmax srid
   footer key:      `temporal`  (self-describing, GeoParquet-style)
        ▼
Apache Iceberg tables   ── snapshots · schema evolution · time travel
        ▼
REST catalog (Apache Polaris)   ── one open protocol, any vendor
        ▼
MobilityDB · MobilityDuck · MobilitySpark   ── same data, no conversion
        ▼
portable bare-name SQL   ── one query, three engines
```

## Try it

▶ **Runnable notebook:** [`examples/mobility-lakehouse-quickstart.ipynb`](https://github.com/MobilityDB/MobilityLakehouse/blob/main/examples/mobility-lakehouse-quickstart.ipynb)
builds this end to end on MobilityDuck — ingest, write, prune, round-trip, Iceberg —
and renders its executed outputs on GitHub.

Build a small lakehouse with MobilityDuck (DuckDB). The same files are read by
MobilityDB and MobilitySpark.

```sql
-- 1. Raw events → typed trajectories
CREATE TABLE trajectories AS
SELECT entity_id,
       tgeogpointSeq(list(TGEOGPOINT(ST_Point(lon, lat), ts) ORDER BY ts)) AS traj
FROM read_csv_auto('events_*.csv', header = true)
GROUP BY entity_id HAVING count(*) >= 3;

-- 2. Write a TemporalParquet shard: lossless value + covering columns
COPY (
  SELECT entity_id,
         asBinary(traj)    AS traj,                  -- canonical value (BLOB)
         Xmin(stbox(traj)) AS xmin, Xmax(stbox(traj)) AS xmax,
         Ymin(stbox(traj)) AS ymin, Ymax(stbox(traj)) AS ymax,
         Tmin(stbox(traj)) AS tmin, Tmax(stbox(traj)) AS tmax,
         SRID(traj)        AS srid
  FROM trajectories
) TO 'lake/year=2026/month=02/day=26/shard_000.parquet' (FORMAT PARQUET);

-- 3. Query, pruned by space and time before any value is read
SELECT entity_id, asText(tgeompointFromBinary(traj))
FROM read_parquet('lake/**/*.parquet')
WHERE tmax >= TIMESTAMPTZ '2026-02-26' AND tmin < TIMESTAMPTZ '2026-02-27'
  AND xmax >= 4.0 AND xmin <= 5.0 AND ymax >= 51.0 AND ymin <= 52.0;
```

The full ingest → annotate → Iceberg → cross-engine round-trip walkthrough is
in [getting-started.md](https://github.com/MobilityDB/MobilityLakehouse/blob/main/getting-started.md).

## Engines

The same lakehouse is read and written by every engine in the ecosystem:

| | MobilityDB (PostgreSQL) | MobilityDuck (DuckDB) | MobilitySpark (Spark) |
| --- | --- | --- | --- |
| Read Iceberg mobility tables | producer | ✓ | ✓ (native runtime) |
| Write TemporalParquet | ✓ | ✓ | ✓ |
| Covering-column pruning | ✓ | ✓ | ✓ |
| Snapshot time travel | — | ✓ | ✓ |
| REST catalog (Polaris) | — | ✓ | ✓ |
| Portable bare-name SQL | ✓ | ✓ | ✓ |

The covering schema is generated once from the MEOS catalog, so every engine
emits identical covering columns — consistency by construction, not by
convention.

## Features

- **Open and lossless** — values survive export→import byte-for-byte; any
  Parquet tool can read the file, MEOS reconstructs the trajectory.
- **Self-describing** — the `temporal` footer makes a file interpretable with
  no MobilityDB installation.
- **Fast filtering** — covering columns prune at the Iceberg manifest and
  Parquet row-group level, no spatial index server.
- **Warehouse semantics** — Iceberg snapshots, schema evolution, time travel.
- **Vendor-neutral catalog** — targets the open Iceberg REST protocol (Apache
  Polaris recommended).
- **Portable compute** — the same bare-name SQL runs on all three engines.

## Roadmap: rasters and Earth Observation

v1 covers **vector** mobility (trajectories). Next, a **raster** table family
joins the same lakehouse:

- **Raquet** — raster-in-Parquet via CARTO QUADBIN tiling, the raster
  counterpart of TemporalParquet.
- **`tquadbin`** — a temporal QUADBIN cell index bridging trajectories and
  raster tiles.
- **Raster sampling along trajectories** — enrich a path from a raster
  (sea state, weather, land cover).
- **Earth-observation satellite imagery** — EO scenes as time-stamped raster
  tiles, co-located with vessel and vehicle trajectories and jointly queryable.

The goal: one lakehouse where moving objects and the Earth-observation context
they move through are first-class and queried together.

## Learn more

- **Live demo** — [AIS Iceberg Explorer](https://ais-explorer-833836401560.europe-west1.run.app/)
- **MobilityDB** — the temporal/spatiotemporal database — <https://github.com/MobilityDB/MobilityDB>
- **MobilityDuck** — the DuckDB extension — <https://github.com/MobilityDB/MobilityDuck>
- **MobilitySpark** — the Spark integration — <https://github.com/MobilityDB/MobilitySpark>
- **Format specification** — [TemporalParquet](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/temporalparquet.md) · [covering columns](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md)
- **Getting started** — the [end-to-end walkthrough](https://github.com/MobilityDB/MobilityLakehouse/blob/main/getting-started.md)
- **Roadmap** — the [ecosystem plan](https://github.com/MobilityDB/MobilityLakehouse/blob/main/ROADMAP.md): temporal data as a first-class Iceberg citizen
