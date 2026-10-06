<!--
Copyright(c) MobilityDB Contributors

This documentation is licensed under a
Creative Commons Attribution-Share Alike 3.0 License
https://creativecommons.org/licenses/by-sa/3.0/
-->

# MobilityLakehouse specifications

The MobilityLakehouse stores temporal values in plain Parquet files that any
engine of the MobilityDB ecosystem reads without conversion. The format is
defined by the following documents.

| Document | What it defines |
|---|---|
| [TemporalParquet](../TemporalParquet.md) | The file format, version 2.0.0: each temporal column is a `BYTE_ARRAY` holding the MEOS-WKB encoding of its values, and a `temporal` footer key, modelled on GeoParquet, describes each column's base type, interpolation, and reference system. |
| [Covering columns](covering-columns.md) | The bounding-box and time-span columns materialised alongside a temporal value, which let Parquet and Iceberg skip data before reading any trajectory. |
| [Table formats](table-formats.md) | How TemporalParquet files are organised into Apache Iceberg and DuckLake tables. |
| [Conformance](conformance.md) | What makes a file a valid MobilityLakehouse table, and what an engine must do to support it. |

TemporalParquet is the entry point: it defines the encoding and the footer
metadata on which the three other documents build.
