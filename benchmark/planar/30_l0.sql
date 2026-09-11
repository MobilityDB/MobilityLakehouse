-- Stage 3 of the planar pipeline: the L0 base layer for one UTC calendar day, in the query-ready
-- `trips` schema with the covering columns of the TemporalParquet specification
-- (MobilityLakehouse spec/covering-columns.md): `trip_bbox` {xmin, ymin, xmax, ymax} and
-- `trip_tspan` {tmin, tmax} derived from the trajectory's box, and `srid`. The same six bounds
-- are also carried as top-level columns trip_xmin .. trip_tmax: a table format's file pruning
-- reads top-level bounds only (pyiceberg writes no manifest bounds for a struct field, and the
-- DuckDB Iceberg and DuckLake scans prune no file on one), so the flat copies are what a catalog
-- skips files on, while the structs keep the GeoParquet covering. Every segment meeting
-- the day is cut to [day, day + 1), so no row crosses midnight. Rows follow the chronological
-- order a feed appended as it arrives delivers, since L0 is the unclustered control. The COPY is
-- appended by run_clean.sh, because COPY ... TO takes a literal path.
--
-- Variables: out (the run directory), day.

CREATE OR REPLACE TEMP TABLE l0 AS
WITH d AS (
  SELECT getvariable('day')::TIMESTAMP AS d0,
         getvariable('day')::TIMESTAMP + INTERVAL 1 DAY AS d1),
cut AS (
  SELECT s.mmsi, s.ship_type, s.segment_type,
    atTime(tgeompointFromEWKB(s.trip), span(d.d0::TIMESTAMPTZ, d.d1::TIMESTAMPTZ, true, false))
      AS trip
  FROM read_parquet(getvariable('out') || '/segments/*.parquet') s, d
  WHERE s.t0 < d.d1 AND s.t1 >= d.d0),
boxed AS (SELECT *, stbox(trip) AS b FROM cut WHERE trip IS NOT NULL)
SELECT
  -- the MMSI as ITU-R M.1371 carries it, a number in a 30-bit field, which any integer of 31 bits
  -- or more holds exactly; BIGINT is the raw zone's type, and Iceberg, whose integers are all
  -- signed, stores it unchanged. Its nine-digit form (ITU-R M.585) is the number zero-padded to
  -- nine digits, a padding no row here needs since stage 1 keeps only ship-station identities.
  -- Lower case, the name PostgreSQL gives an unquoted identifier, so the same SQL and the same
  -- file round-trip through it unchanged
  mmsi::BIGINT AS mmsi,
  ship_type,
  segment_type,
  asEWKB(trip) AS trip,
  {'xmin': Xmin(b), 'ymin': Ymin(b), 'xmax': Xmax(b), 'ymax': Ymax(b)} AS trip_bbox,
  {'tmin': Tmin(b)::TIMESTAMP, 'tmax': Tmax(b)::TIMESTAMP} AS trip_tspan,
  Xmin(b) AS trip_xmin, Ymin(b) AS trip_ymin, Xmax(b) AS trip_xmax, Ymax(b) AS trip_ymax,
  Tmin(b)::TIMESTAMP AS trip_tmin, Tmax(b)::TIMESTAMP AS trip_tmax,
  SRID(trip) AS srid,
  getvariable('day')::DATE AS dt
FROM boxed
ORDER BY Tmin(b), MMSI;
