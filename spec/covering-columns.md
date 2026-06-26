<!--
Copyright(c) MobilityDB Contributors

This documentation is licensed under a
Creative Commons Attribution-Share Alike 3.0 License
https://creativecommons.org/licenses/by-sa/3.0/
-->

# Covering columns

A temporal value is stored as an opaque `BYTE_ARRAY` (MEOS-WKB). Parquet and
Iceberg cannot prune on an opaque blob. **Covering columns** are primitive
bounding-box columns materialised *alongside* the value, so the engine gets
min/max statistics it can use to skip data before reading any trajectory.

This is the single highest-leverage performance feature of the lakehouse: a
space-and-time predicate prunes whole files (Iceberg manifests) and row groups
(Parquet), with **no spatial-aware engine and no spatial extension**.

## The columns

| Temporal class | Box | Covering columns |
|---|---|---|
| spatial (`tgeompoint`, `tgeogpoint`, `tgeometry`, `tgeography`, `tcbuffer`, `tnpoint`, `tpose`, `trgeometry`) | `STBOX` | `xmin xmax ymin ymax` (+ `zmin zmax` for 3D) · `tmin tmax` · `srid` |
| numeric (`tint`, `tfloat`, `tbigint`) | `TBOX` | `vmin vmax` · `tmin tmax` |

The value column stays the lossless source of truth; the covering columns are a
denormalised derivation of the value's bounding box.

## Writing them

The covering columns are computed from the value with the engine's box
accessors. In MobilityDuck:

```sql
SELECT
  asBinary(traj)    AS traj,                       -- canonical value (BLOB)
  Xmin(stbox(traj)) AS xmin, Xmax(stbox(traj)) AS xmax,
  Ymin(stbox(traj)) AS ymin, Ymax(stbox(traj)) AS ymax,
  Tmin(stbox(traj)) AS tmin, Tmax(stbox(traj)) AS tmax,
  SRID(traj)        AS srid
FROM trajectories;
```

## Pruning with them

A bounding-box predicate is a scalar AND-chain over the covering columns — the
engine evaluates it against column statistics and skips non-intersecting files
and row groups:

```sql
SELECT entity_id, asText(tgeompointFromBinary(traj))
FROM read_parquet('lake/**/*.parquet')
WHERE tmax >= TIMESTAMPTZ '2026-02-26' AND tmin < TIMESTAMPTZ '2026-02-27'
  AND xmax >= 4.0 AND xmin <= 5.0 AND ymax >= 51.0 AND ymin <= 52.0;
```

Aligned with GeoParquet 1.1 `covering.bbox`: the same columns serve Parquet
row-group pruning and Iceberg manifest-level file pruning.

## One source of truth across engines

The set of covering columns per temporal type is not hand-written per engine.
It is described once, declaratively, in the MEOS catalog (a `temporalCovering`
descriptor) and every binding generates the identical covering schema from it.
So a file written by MobilityDuck, MobilityDB, or MobilitySpark carries the
same covering columns, and prunes the same way, by construction rather than by
convention.

## Related

- [TemporalParquet](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/temporalparquet.md) — the file format these columns live in
- [Getting started](https://github.com/MobilityDB/MobilityLakehouse/blob/main/getting-started.md) — write and query a pruned lakehouse
