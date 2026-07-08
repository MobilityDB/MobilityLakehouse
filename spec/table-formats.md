<!--
Copyright(c) MobilityDB Contributors

This documentation is licensed under a
Creative Commons Attribution-Share Alike 3.0 License
https://creativecommons.org/licenses/by-sa/3.0/
-->

# Table formats

The lakehouse stack has three open, independent layers:

```
engines            MobilityDB · MobilityDuck · MobilitySpark · Trino · Flink
catalog            Iceberg REST protocol (Apache Polaris) · DuckLake SQL catalog
table format       Apache Iceberg · DuckLake · (Delta, Hudi, Paimon)
open file format   TemporalParquet  =  Parquet + MEOS-WKB value + covering columns
object storage     S3 · Azure · GCS · local
```

The layers are deliberately decoupled: bytes live in object storage, an open
**file format** describes each file, an open **table format** groups files into
ACID tables with snapshots and time travel, and a **catalog** makes those tables
discoverable to any engine.

## Where MobilityDB integrates — and why it is *below* the table format

**MobilityDB contributes to the file-format layer, not the table-format layer.**
This is a deliberate architectural choice, and it is what keeps the table format
interchangeable.

The reason is concrete: **every open table format has a closed type system.**
Iceberg, DuckLake, Delta, and Hudi all fix their physical types, and none has a
native temporal or spatiotemporal type. A `tgeompoint` trajectory is *always*
physically a `binary` column in a manifest — there is no way to teach the table
format what a trajectory is. So the ecosystem does **not** bind mobility support
to any one table format. Instead it contributes two things at the file level,
both plain Parquet constructs:

1. **A lossless value encoding** — the trajectory as compact MEOS-WKB in a
   Parquet `BYTE_ARRAY`, reconstructed by MEOS on read
   ([TemporalParquet](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/temporalparquet.md)).
2. **Generated covering columns** — scalar bounding-box and time-extent columns
   (`xmin … tmax`, `srid`), GeoParquet 1.1 `covering.bbox`-aligned, so the table
   format's *existing* min/max, partition, and manifest machinery prunes files
   and row groups before any trajectory is read
   ([covering columns](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md)).

Because both are ordinary Parquet — not table-format extensions — the table
format sitting on top is a swappable choice, and the covering schema is
generated once from the MEOS catalog and is **format-agnostic**. Adopting a
different table format changes the catalog protocol and each engine's table-scan
entry point; it does **not** change the format contract, the covering schema, or
the generated bindings.

## Apache Iceberg — the v1 default

[Apache Iceberg](https://iceberg.apache.org/) is the v1 table format because it
has the broadest multi-engine reach (Spark, Trino, Flink, DuckDB, PyIceberg all
read it natively) and an open **REST catalog protocol** that decouples the
lakehouse from any vendor. The recommended OSS catalog server is
[Apache Polaris](https://polaris.apache.org/); the ecosystem targets the
protocol, not a specific server, so Iceberg-on-Glue or Iceberg-on-Nessie users
are served for free.

## DuckLake — a supported second table format

[DuckLake](https://ducklake.select/) (v1.0, production-ready since April 2026)
takes a different design: instead of file-based JSON/Avro manifests, it stores
all table metadata in a **SQL database** (DuckDB, PostgreSQL, SQLite, or MySQL).
Query planning is then a single SQL query against the catalog rather than a walk
of many small metadata files, and small inserts/updates can be inlined into the
catalog, sidestepping the small-file problem. Clients exist for DuckDB, Apache
Spark, Trino, and Apache DataFusion.

DuckLake is a natural second option for this ecosystem for two reasons:

- **It fits the reference engine.** MobilityDuck (DuckDB) is the engine behind
  the [AIS Iceberg Explorer](https://ais-explorer-833836401560.europe-west1.run.app/)
  and the runnable examples. DuckLake removes the file-based-catalog overhead on
  exactly the single-node, embedded, and development paths where MobilityDuck is
  strongest.
- **It reuses the same substrate.** DuckLake's data and delete files are
  Iceberg-compatible Parquet, so the identical TemporalParquet value column and
  generated covering columns work **unchanged**, and moving a table between
  Iceberg and DuckLake is a metadata-only operation. DuckLake is therefore
  *additive over the same file substrate*, not a competing rewrite.

Because MobilityDB integrates below the table format (above), supporting DuckLake
needs no change to the format contract or the generated covering schema — only a
DuckLake catalog/scan entry point alongside the Iceberg one, per engine.

## Delta Lake, Hudi, Paimon

These are readable at the file-and-covering level like any Parquet dataset — an
engine that opens the underlying Parquet gets the lossless value column and the
prunable covering columns. They are not primary targets, but nothing in the
format contract precludes them, precisely because the mobility contribution sits
below the table format.

## Summary

| | Fixed by the ecosystem | Chosen per deployment |
| --- | --- | --- |
| File substrate | TemporalParquet (MEOS-WKB value + covering columns) | — |
| Covering schema | generated once from the MEOS catalog, format-agnostic | — |
| Decoder | MEOS (via each engine's binding, generated) | — |
| Table format | — | Iceberg (default) · DuckLake · … |
| Catalog | — | Iceberg REST / Polaris · DuckLake SQL catalog |

The table format is the deployment's choice; the mobility contract underneath it
is the same in every case.

## See also

- [TemporalParquet](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/temporalparquet.md) — the open file format
- [Covering columns](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md) — the pruning mechanism
- [Conformance](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/conformance.md) — conformant files and engines
- [Roadmap](https://github.com/MobilityDB/MobilityLakehouse/blob/main/ROADMAP.md) — the ecosystem plan
