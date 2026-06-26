<!--
Copyright(c) MobilityDB Contributors

This documentation is licensed under a
Creative Commons Attribution-Share Alike 3.0 License
https://creativecommons.org/licenses/by-sa/3.0/
-->

# TemporalParquet specification

TemporalParquet is the open file format of the MobilityLakehouse: a Parquet
footer-metadata convention, modelled directly on [GeoParquet](https://geoparquet.org/),
that makes MobilityDB temporal columns self-describing and portable. A file is
plain Parquet — readable by any Parquet tool — and carries enough metadata for
any engine to reconstruct the temporal values without a MobilityDB installation.

## File structure

Each column carrying a temporal type is a `BYTE_ARRAY` (logical type `NONE`);
each row value is the MEOS-WKB encoding of the temporal value, and nulls are
Parquet nulls. The encoding is the MEOS C library's battle-tested WKB; this
spec defines the metadata, not the bytes.

The Parquet file's `key_value_metadata` carries a `temporal` key whose value is
a JSON document describing each temporal column. It coexists with GeoParquet's
`geo` key — a single file may have both.

```jsonc
{
  "version": "1.0.0",
  "primary_temporal_column": "traj",
  "columns": {
    "traj": {
      "encoding": "MEOS-WKB",
      "encoding_version": "1.0",
      "base_type": "tgeompoint",
      "subtype": "Sequence",
      "interpolation": "linear",
      "srid": 4326,
      "geodetic": false,
      "has_z": false
    }
    /* one entry per temporal column */
  }
}
```

## Type coverage

| Type | `base_type` | Notes |
|---|---|---|
| `tbool`, `tint`, `tfloat`, `tbigint`, `ttext` | each as itself | scalar temporals |
| `tgeompoint`, `tgeogpoint` | `tgeompoint` / `tgeogpoint` | spatial-temporal; `srid` + `geodetic` + `has_z` populated |
| `tgeometry`, `tgeography` | `tgeometry` / `tgeography` | general spatial-temporal |
| `th3index` | `th3index` | spatial via `h3_resolution` |
| `tpcpoint`, `tpcpatch` | each as itself | temporal point-cloud types |
| `tcbuffer`, `tnpoint`, `tpose`, `trgeometry` | each as itself | extended temporal types |
| `stbox`, `tbox`, `tpcbox` | each as itself | bounding boxes |
| spans, spansets, sets | each as itself | time/value ranges and sets |

`subtype` (`Instant` / `Sequence` / `SequenceSet`) applies only to lifted
temporal types; span/set/box columns omit it.

### Optional self-describing fields

These let a consumer decide whether a column is usable for a workload **without
decoding any row**:

| Field | Applies to | Meaning |
|---|---|---|
| `srid` | spatial-temporal types | EPSG code of the column's CRS |
| `geodetic` | `tgeogpoint`, `tgeography` | `true` ⇒ spheroidal-metre math |
| `has_z` | spatial-temporal types | column carries a Z dimension |
| `h3_resolution` | `th3index` | resolution `[0,15]` every cell was produced at |

## Encoding versioning

`encoding_version` is `MAJOR.MINOR` of the WKB schema. New WKB tags bump MINOR;
breaking layout changes bump MAJOR. Readers must refuse a file whose MAJOR
exceeds what they support.

## Geodetic distances

`tgeompoint` stores coordinates in the input CRS and computes **Euclidean**
distances in that coordinate space — `length()` over a WGS-84 trajectory
returns degrees, not metres. `tgeogpoint` carries the same MEOS-WKB bytes with
the geodetic flag set, routing all spatial math through the spheroidal engine —
lengths and speeds in **metres**.

For distance or speed analytics, prefer `tgeogpoint` (`"geodetic": true`). The
geodetic flag is self-describing in MEOS-WKB: a file written with
`asBinary(tgeogpointSeq(...))` reconstructs as a geodetic sequence on any engine
that calls `tgeogpointFromBinary(blob)`.

## Covering columns

Alongside the value column, a TemporalParquet writer materialises primitive
**covering columns** — `xmin/xmax/ymin/ymax[/zmin/zmax]`, `tmin/tmax`, `srid`
for spatial types; `vmin/vmax`, `tmin/tmax` for numeric types. These give the
Parquet/Iceberg engine min/max statistics for row-group and manifest pruning,
aligned with GeoParquet 1.1 `covering.bbox`. See
[covering-columns.md](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md).

## Relationship to the lakehouse

TemporalParquet is the open **lake** substrate: plain files, no catalog
required. The **lakehouse** layer registers those files as Apache Iceberg
tables, where the covering columns become Iceberg column statistics and the
catalog prunes whole files before reading. The format is unchanged by Iceberg —
the same files work with or without a catalog.

## Related

- [Covering columns](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md) — the pruning mechanism
- [Getting started](https://github.com/MobilityDB/MobilityLakehouse/blob/main/getting-started.md) — write and read a TemporalParquet lakehouse
- [GeoParquet](https://geoparquet.org/) — the spatial-Parquet standard this is modelled on
- [MobilityDuck](https://github.com/MobilityDB/MobilityDuck) — reference implementation on the read/write path
