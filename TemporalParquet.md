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

This is version 2.0.0 of the specification. It follows GeoParquet 2.0.0: every
concept GeoParquet defines — the coordinate reference system, the axis order,
the interpretation of edges, and the bounding box covering — keeps GeoParquet's
meaning and vocabulary here, and TemporalParquet adds only what a value that
varies over time needs beyond it.

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
  "version": "2.0.0",
  "primary_temporal_column": "traj",
  "columns": {
    "traj": {
      "encoding": "MEOS-WKB",
      "encoding_version": "1.0",
      "base_type": "tgeompoint",
      "subtype": "Sequence",
      "interpolation": "linear",
      "srid": 25832,
      "crs": { /* PROJJSON of EPSG:25832 */ },
      "edges": "planar",
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
| `tgeompoint`, `tgeogpoint` | `tgeompoint` / `tgeogpoint` | spatial-temporal; `srid`, `crs`, `edges`, `geodetic` and `has_z` populated |
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
| `srid` | spatial-temporal types | spatial reference identifier the values carry; `0` when the CRS is undefined or unknown |
| `crs` | spatial-temporal types | the CRS as inline PROJJSON, or `null` when undefined or unknown, as GeoParquet's column-metadata `crs` |
| `edges` | spatial-temporal types | how the path between two instants is interpreted, in GeoParquet's vocabulary: `planar` or `spherical` |
| `geodetic` | spatial-temporal types | `true` exactly when `edges` is not `planar` |
| `has_z` | spatial-temporal types | column carries a Z dimension |
| `h3_resolution` | `th3index` | resolution `[0,15]` every cell was produced at |

## Encoding versioning

`encoding_version` is `MAJOR.MINOR` of the WKB schema. New WKB tags bump MINOR;
breaking layout changes bump MAJOR. Readers must refuse a file whose MAJOR
exceeds what they support.

## Coordinate reference systems

A spatial-temporal column may be in any coordinate reference system,
geographic or projected, exactly as a GeoParquet geometry column may.
TemporalParquet prescribes no CRS, neither for storing a column nor for
computing on it.

The CRS travels with the values: every MEOS-WKB value carries its SRID, as the
CRS of a GeoParquet 2.0 geometry column travels on its Parquet logical type.
`srid` repeats it so that a consumer reads it without decoding a row. `crs`
restates the same CRS in the form GeoParquet's column-metadata `crs` uses,
inline PROJJSON, so that a reader obtains a complete definition without
resolving an identifier against a registry; it must not describe a CRS other
than the one `srid` names. As in GeoParquet, an undefined or unknown CRS is
`srid` `0` together with `crs` `null`. When `crs` is absent, the CRS is the one
`srid` names.

Coordinates are ordered as GeoParquet orders them: x is easting or longitude
and y is northing or latitude, whatever axis order the CRS itself defines.

## Edges

The base type states how the path between two instants is interpreted, in the
terms of the Parquet geospatial types and of GeoParquet's `edges`:

| `base_type` | Parquet counterpart | CRS | `edges` |
|---|---|---|---|
| `tgeompoint`, `tgeometry` | `GEOMETRY` | any | `planar` |
| `tgeogpoint`, `tgeography` | `GEOGRAPHY` | geographic, longitude/latitude | `spherical` |

A geodetic value moves between two instants along the shortest path on the
sphere, which is GeoParquet's `spherical`. `geodetic` is `true` exactly for the
second row. The flag is also carried in the MEOS-WKB, so a file written with
`asBinary(tgeogpointSeq(...))` reconstructs as a geodetic sequence on any engine
that calls `tgeogpointFromBinary(blob)`.

## Covering columns

Alongside the value column, a TemporalParquet writer materialises **covering
columns**: struct columns at the root of the schema whose fields give the
Parquet/Iceberg engine min/max statistics for row-group and manifest pruning.

| Covering | Applies to | Fields |
|---|---|---|
| `bbox` | spatial types | `xmin`, `ymin`, [`zmin`,] `xmax`, `ymax`[, `zmax`] |
| `tspan` | every temporal type | `tmin`, `tmax` |
| `vspan` | numeric types | `vmin`, `vmax` |

The `bbox` covering column is a GeoParquet bounding box column: its four or six
fields are in that order and of one floating-point type, it has the repetition
of its temporal column, and it holds a value exactly when the temporal column
does. See [covering-columns.md](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md).

A column's coverings are declared in `temporal`, in the shape GeoParquet uses
for `covering`: a mapping from each bound to the path of the field that carries
it, so a consumer finds the columns by reading the metadata rather than by
guessing their names.

```jsonc
"traj": {
  "encoding": "MEOS-WKB",
  "base_type": "tgeompoint",
  "covering": {
    "bbox": {
      "xmin": ["traj_bbox", "xmin"], "ymin": ["traj_bbox", "ymin"],
      "xmax": ["traj_bbox", "xmax"], "ymax": ["traj_bbox", "ymax"]
    },
    "tspan": {
      "tmin": ["traj_tspan", "tmin"], "tmax": ["traj_tspan", "tmax"]
    }
  }
}
```

`bbox` is GeoParquet's covering encoding, with GeoParquet's rules. `tspan` and
`vspan` are TemporalParquet's, built the same way. They are declared here rather
than in `geo` because `covering` is a member of a *geometry* column's metadata
there, and a temporal column is not a geometry column — see
[conformance.md](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/conformance.md)
for the rules that govern the two keys living in one file.

`tspan` is the smallest useful extension of GeoParquet's own vocabulary, and it
is offered as such: the keys of `covering` are encodings, and a `tspan`
encoding makes a spatial-and-temporal pruning predicate expressible in
GeoParquet itself, additively and compatibly with every file already written.

GeoParquet defines the bounding box covering on its main branch; its 2.0.0-rc.1
release leaves `covering` out and relies on the Parquet format's own geospatial
statistics, which a `GEOMETRY` or `GEOGRAPHY` column carries for every row
group. A temporal column is a `BYTE_ARRAY` with no geospatial logical type, so
it gets no such statistics, and its covering columns are what gives it
statistics on every engine. The Parquet statistics' `BoundingBox` already
describes its optional `mmin`/`mmax` as usable for a timestamp, so the other
candidate is addressed to Parquet rather than to GeoParquet: a temporal
statistic in the column metadata, so that a time predicate prunes natively on a
column whose logical type is not `GEOMETRY` or `GEOGRAPHY`. Both candidates, and
the questions the published GeoParquet artifacts raise, are stated in
[Extending GeoParquet in MobilityDB](https://github.com/MobilityDB/MobilityDB/wiki/Extending-GeoParquet-in-MobilityDB).

Either way the covering columns are unaffected: they are ordinary Parquet
columns with ordinary statistics, and they prune on any engine with no metadata
convention at all. What a standard buys them is discoverability.

## Relationship to the lakehouse

TemporalParquet is the open **lake** substrate: plain files, no catalog
required. The **lakehouse** layer registers those files as Apache Iceberg
tables, where the covering columns become Iceberg column statistics and the
catalog prunes whole files before reading. The format is unchanged by Iceberg —
the same files work with or without a catalog.

## Related

- [Covering columns](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md) — the pruning mechanism
- [Getting started](https://github.com/MobilityDB/MobilityLakehouse/blob/main/getting-started.md) — write and read a TemporalParquet lakehouse
- [GeoParquet](https://geoparquet.org/) — the spatial-Parquet standard this follows
- [MobilityDuck](https://github.com/MobilityDB/MobilityDuck) — reference implementation on the read/write path
