<!--
Copyright(c) MobilityDB Contributors

This documentation is licensed under a
Creative Commons Attribution-Share Alike 3.0 License
https://creativecommons.org/licenses/by-sa/3.0/
-->

# Roadmap

Making temporal and spatiotemporal data a first-class citizen of the open
lakehouse, across the whole MobilityDB ecosystem.

## The approach

Two facts shape everything below.

- **A native temporal type inside Iceberg is unreachable.** Iceberg's type
  system is closed; a `tgeompoint` is always physically `binary` in a manifest.
  So the goal is not to teach Iceberg about trajectories — it is to make the
  *prunable scalars derivable*, so Iceberg's existing min/max, partition, and
  manifest machinery does the work. That lever is the
  [covering columns](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md)
  (GeoParquet 1.1 `covering.bbox`-aligned): manifest- and row-group-level
  pruning with no spatial-aware engine.
- **Iceberg support is one generated contract, not N integrations.** The
  contract is injected once at the MEOS catalog; every binding and engine emits
  the identical covering schema by regeneration, with no hand special-cases.
  This is what makes it scale to new temporal types and new engines for free.
- **The table format is a swappable choice, not a lock-in.** Because the value
  and covering columns are plain Parquet *below* the table format, Iceberg is
  the v1 default but not the only option:
  [DuckLake](https://ducklake.select/) — which keeps table metadata in a SQL
  catalog — is a supported second table format. Its data files are
  Iceberg-compatible, so the same TemporalParquet value and generated covering
  columns apply unchanged and migration is metadata-only. See
  [table formats](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/table-formats.md).

## Status

| Area | Status |
| --- | --- |
| Open file format ([TemporalParquet](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/temporalparquet.md)) + [covering columns](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/covering-columns.md) | specified |
| [Table-format layer](https://github.com/MobilityDB/MobilityLakehouse/blob/main/spec/table-formats.md) — Iceberg default, DuckLake supported | specified |
| Reference deployment ([AIS Iceberg Explorer](https://ais-explorer-833836401560.europe-west1.run.app/)) | live |
| Runnable [examples](https://github.com/MobilityDB/MobilityLakehouse/blob/main/examples) on MobilityDuck | live |
| Catalog source of truth — the `temporalCovering` descriptor | MEOS-API [#24](https://github.com/MobilityDB/MEOS-API/pull/24) (merged) |
| Generated covering-column projection | MEOS-API [#25](https://github.com/MobilityDB/MEOS-API/pull/25) (merged) |
| Covering pruning over vector trajectories | proven (examples + benchmark) |
| Per-binding emission and engine readers | next, generated from the descriptor |
| MobilityDB producer / streaming sink | decision-gated (see Open decisions) |
| Raster / Raquet / Earth Observation | open PRs (see Roadmap below) |

The catalog layer — the single source of truth — is built: descriptor #24
plus its projection #25, every referenced symbol verified present in the
catalog and every covered type a real MEOS type.

## Plan by lane

### The three engines

- **MobilityDuck** (the AIS Iceberg Explorer engine) — covering-column
  emission and a `temporal_iceberg_scan` over the native DuckDB Iceberg path.
  The box accessors already exist; the covering schema is generated from the
  descriptor.
- **MobilitySpark** — native Iceberg runtime; the only addition is
  temporal-decode UDFs over the value-plus-covering payload, generated rather
  than hand-written.
- **MobilityDB (PostgreSQL)** — the lake's authoritative producer. Whether it
  is also an Iceberg client (an FDW reader) or producer-only is the central
  open decision.

### Bindings (generated, not hand-coded)

Each binding's own generator consumes the catalog descriptor and emits the
identical covering columns and value codec. PyMEOS is the reference codec;
JMEOS backs the Flink and Kafka stream layers.

### Streaming

A contract-compliant Iceberg append sink (one snapshot per checkpoint) for
Flink and Kafka, materialising the covering columns at write time — temporal
pruning that generic sinks do not provide.

## Roadmap: rasters and Earth Observation

Vector mobility is v1. A raster table family joins the same lakehouse:

- **Raquet** — raster-in-Parquet via CARTO QUADBIN tiling
  ([#1217](https://github.com/MobilityDB/MobilityDB/pull/1217)).
- **`tquadbin`** — a temporal QUADBIN cell index bridging trajectories and
  raster tiles ([#1210](https://github.com/MobilityDB/MobilityDB/pull/1210)).
- **Raster sampling along trajectories** — enrich a path from a raster
  ([#1216](https://github.com/MobilityDB/MobilityDB/pull/1216),
  [#1218](https://github.com/MobilityDB/MobilityDB/pull/1218)).
- **Earth-observation satellite imagery** — EO scenes as time-stamped raster
  tiles, co-located with trajectories and jointly queryable.

## Open decisions

1. **Ratify the contract** (TemporalParquet value plus covering columns) as the
   single ecosystem source of truth, generated from MEOS-API #24 / #25.
2. **MobilityDB's role** — producer-only (recommended), an FDW reader, or defer.
3. **Catalog stance** — Apache Polaris as the recommended OSS server over the
   open Iceberg REST protocol, versus REST-protocol-neutral.
4. **A MEOS export gap** — time-only covering for `tbool` / `ttext` needs a span
   lower/upper bound accessor; it is surfaced as a MEOS-C gap to close, not
   filled binding-side. Point-cloud and cell-index types fold into the spatial
   class once a uniform temporal-to-`STBOX` converter is confirmed.
5. **Publishing MobilityDuck** to the DuckDB community-extensions repository for
   a released DuckDB — the step that makes the examples one-command reproducible.

## What is independent of the next ecosystem pin

The catalog source of truth, the format specification, the examples on the
current engine, the per-binding generator logic, and these documents are all
independent of the pinned `libmeos`. The pin gates only the proof that each
binding builds against it, a MobilityDB producer change, and the MEOS-C span
accessor — not the contract itself.
