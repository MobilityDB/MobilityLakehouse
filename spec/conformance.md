<!--
Copyright(c) MobilityDB Contributors

This documentation is licensed under a
Creative Commons Attribution-Share Alike 3.0 License
https://creativecommons.org/licenses/by-sa/3.0/
-->

# Conformance

What it means for a file to be a valid MobilityLakehouse table, and for an
engine to support it. These are the acceptance criteria the ecosystem holds
every engine to, so the same data round-trips and prunes the same way
everywhere.

## A conformant file

A TemporalParquet file is conformant when:

1. **Value column** — each temporal column is a `BYTE_ARRAY` of canonical
   MEOS-WKB; nulls are Parquet nulls.
2. **Footer** — the file's `key_value_metadata` carries a `temporal` key whose
   JSON describes each temporal column (`base_type`, `subtype`, `interpolation`,
   `srid`, `geodetic`, `has_z`, `encoding_version`), per the
   [TemporalParquet spec](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/temporalparquet.md).
3. **Covering columns** — for each temporal column, the primitive covering
   columns its class requires are present and correct, per the
   [covering-columns spec](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md):
   spatial → `xmin xmax ymin ymax [zmin zmax] tmin tmax srid`; numeric →
   `vmin vmax tmin tmax`.

A file may carry additional columns and GeoParquet's `geo` key; they do not
affect conformance.

## A conformant engine

An engine is conformant when it passes three checks for every temporal type it
supports — the same three-condition shape the ecosystem uses for parity:

| Check | What it proves |
| --- | --- |
| **Writes** | the engine produces a conformant file (value + footer + covering columns) |
| **Reads losslessly** | a value written by any engine reconstructs byte-for-byte; the bounds of the reconstructed value equal its covering columns |
| **Prunes** | a bounding-box-and-time predicate over the covering columns skips non-intersecting files and row groups |

The runnable [examples](https://github.com/MobilityDB/MobilityLakehouse/blob/main/examples)
demonstrate all three for MobilityDuck: the
[quickstart](https://github.com/MobilityDB/MobilityLakehouse/blob/main/examples/mobility-lakehouse-quickstart.ipynb)
(write + lossless round-trip),
[lake-analytics](https://github.com/MobilityDB/MobilityLakehouse/blob/main/examples/lake-analytics.ipynb)
(read), and
[pruning-at-scale](https://github.com/MobilityDB/MobilityLakehouse/blob/main/examples/pruning-at-scale.ipynb)
(prune). The same checks, run across MobilityDB, MobilityDuck, and MobilitySpark
on a shared corpus, are the cross-engine conformance matrix.

## The covering schema is generated, not hand-written

The set of covering columns per temporal type is **not** authored per engine. It
is described once, declaratively, in the MEOS catalog — the `temporalCovering`
descriptor (MEOS-API [#24](https://github.com/MobilityDB/MEOS-API/pull/24)) and
its projection (MEOS-API [#25](https://github.com/MobilityDB/MEOS-API/pull/25)).
Each binding's generator reads that descriptor and emits the identical covering
schema and value codec. So conformance is consistency *by construction*: adding
a temporal type adds one descriptor entry, and every engine produces the same
covering columns for it on regeneration.

## How each engine produces and consumes

| Engine | Produce | Consume |
| --- | --- | --- |
| **MobilityDB** (PostgreSQL) | the TemporalParquet exporter (`asEWKB` value + covering columns), e.g. the `scripts/parquet` PoC | the importer, or any engine below |
| **MobilityDuck** (DuckDB) | `COPY ( SELECT asBinary(traj), Xmin(stbox(traj)), … ) TO '…' (FORMAT PARQUET)` | `read_parquet(…)` + `tgeompointFromBinary` / `tgeogpointFromBinary` |
| **MobilitySpark** (Spark) | DataFrame write of the value + covering columns | native Iceberg runtime + temporal-decode UDFs |

The producer SQL differs only in surface syntax; the covering columns it writes
are the same on every engine because they come from the same descriptor.

## Catalog layer

Registering a conformant tree as an Apache Iceberg table is orthogonal to the
file format: the covering columns become Iceberg column statistics (manifest
pruning) and the `temporal` footer is opaque to the catalog. The lakehouse
targets the open Iceberg **REST Catalog** protocol, with Apache Polaris as the
recommended OSS server; no catalog is required for the open lake substrate.

## Coverage

The covering descriptor covers the spatial, numeric, and time-only temporal
types (the last — `tbool`, `ttext` — carry `tmin/tmax` only, no spatial box).
**Point-cloud and cell-index types** (`tpcpoint`, `tpcpatch`, `th3index`,
`tquadbin`) fold into the spatial class once a uniform temporal-to-`STBOX`
converter is confirmed in the catalog.
