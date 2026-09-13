-- The cleaned segments by type, as the paper's segment table and its dataset paragraph state them,
-- read from the L0 rows of one run_clean.sh output: rows and vessels per type, the median and mean
-- duration, the mean number of instants and the mean length; then all rows over all vessels, and
-- the stationary rows lasting more than a day, which reads 0 because L0 cuts every segment at
-- midnight. A duration is the time span of the row in minutes, a length is in kilometres of the
-- run's metric reference system.
--
-- One L0 day is one row group of trajectory values, which every thread decodes whole, so the read
-- runs on two threads under a memory limit two of them fit in, and keeps no insertion order.
--
-- Variables: out (the run directory).

SET memory_limit = '2500MB';
SET threads = 2;
SET preserve_insertion_order = false;

CREATE OR REPLACE TEMP TABLE l0 AS
SELECT MMSI, segment_type,
  epoch(trip_tmax - trip_tmin) / 60 AS minutes,
  numInstants(tgeompointFromEWKB(trip)) AS points,
  length(tgeompointFromEWKB(trip)) / 1000 AS km
FROM read_parquet(getvariable('out') || '/L0/*/*/*.parquet');

SELECT segment_type, count(*) AS segments, count(DISTINCT MMSI) AS vessels,
  round(median(minutes), 1) AS median_min, round(avg(minutes), 1) AS mean_min,
  round(avg(points)) AS avg_points, round(avg(km), 2) AS avg_km
FROM l0 GROUP BY segment_type ORDER BY segment_type;

SELECT count(*) AS segments, count(DISTINCT MMSI) AS vessels,
  count(*) FILTER (WHERE segment_type = 'Stationary' AND minutes > 24 * 60)
    AS stationary_over_a_day
FROM l0;
