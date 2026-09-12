-- Checks of the planar L0 layer of one run_clean.sh output, each counting what it examines beside
-- what breaks the rule, so a zero reads as "none broke it" only when the examined count is not
-- zero.
--
-- Variables: out (the run directory), mid_csv (the MID table stage 1 reads).

CREATE OR REPLACE TEMP TABLE l0 AS
SELECT * FROM read_parquet(getvariable('out') || '/L0/*/*/*.parquet');
CREATE OR REPLACE TEMP TABLE seg AS
SELECT * FROM read_parquet(getvariable('out') || '/segments/*.parquet');
CREATE OR REPLACE TEMP TABLE stp AS
SELECT * FROM read_parquet(getvariable('out') || '/stops/*.parquet');

-- 1. Rows, segments and stops by type.
SELECT segment_type, count(*) AS l0_rows, count(DISTINCT MMSI) AS vessels
FROM l0 GROUP BY segment_type ORDER BY segment_type;
SELECT (SELECT count(*) FROM seg WHERE segment_type = 'Stationary') AS stationary_segments,
  (SELECT count(*) FROM stp) AS stopped_runs,
  (SELECT count(*) FROM seg WHERE segment_type = 'In motion') AS moving_segments;

-- 2. The covering columns equal the bounds of the stored trajectory, its instants read in UTC
-- whatever the session's zone: the time bounds are UTC, so a trajectory whose instants were built
-- in another zone differs from them here.
SELECT count(*) AS rows,
  count(*) FILTER (WHERE trip_bbox.xmin <> Xmin(stbox(tgeompointFromEWKB(trip)))
                      OR trip_bbox.ymin <> Ymin(stbox(tgeompointFromEWKB(trip)))
                      OR trip_bbox.xmax <> Xmax(stbox(tgeompointFromEWKB(trip)))
                      OR trip_bbox.ymax <> Ymax(stbox(tgeompointFromEWKB(trip)))
                      OR trip_tspan.tmin <> timezone('UTC', startTimestamp(tgeompointFromEWKB(trip)))
                      OR trip_tspan.tmax <> timezone('UTC', endTimestamp(tgeompointFromEWKB(trip))))
    AS bounds_differ
FROM l0;

-- 2b. The top-level copies trip_xmin .. trip_tmax equal the struct fields they copy.
SELECT count(*) AS rows,
  count(*) FILTER (WHERE trip_xmin IS DISTINCT FROM trip_bbox.xmin
                      OR trip_ymin IS DISTINCT FROM trip_bbox.ymin
                      OR trip_xmax IS DISTINCT FROM trip_bbox.xmax
                      OR trip_ymax IS DISTINCT FROM trip_bbox.ymax
                      OR trip_tmin IS DISTINCT FROM trip_tspan.tmin
                      OR trip_tmax IS DISTINCT FROM trip_tspan.tmax)
    AS flat_differs_from_struct
FROM l0;

-- 2c. Every vessel identifier is an individual ship-station MMSI (ITU-R M.585: the three-digit
-- MID, first digit 2 to 7, then six digits) whose MID is allocated, the rule stage 1 keeps, held
-- in the raw zone's BIGINT.
SELECT count(*) AS rows, string_agg(DISTINCT typeof(mmsi), ',') AS mmsi_type,
  count(*) FILTER (WHERE mmsi NOT BETWEEN 200000000 AND 799999999) AS not_a_ship_station,
  count(*) FILTER (WHERE mmsi // 1000000 NOT IN (SELECT mid FROM read_csv(getvariable('mid_csv'))))
    AS mid_not_allocated
FROM l0;

-- 2d. Each segment's trajectories start at its t0 and end at its t1, their instants read in UTC:
-- t0 and t1 are the report times of the clean points, so this ties every stored instant to the
-- report it comes from.
SELECT count(*) AS segments,
  count(*) FILTER (WHERE t0 <> timezone('UTC', startTimestamp(tgeompointFromEWKB(trip)))
                      OR t1 <> timezone('UTC', endTimestamp(tgeompointFromEWKB(trip)))
                      OR t0 <> timezone('UTC', startTimestamp(tgeompointFromEWKB(trip_wgs84)))
                      OR t1 <> timezone('UTC', endTimestamp(tgeompointFromEWKB(trip_wgs84))))
    AS instants_off_report_time
FROM seg;

-- 3. Consecutive segments of a piece meet: each segment after the first starts at the instant
-- the one before it ends.
SELECT count(*) AS consecutive_pairs, count(*) FILTER (WHERE t0 <> prev_t1) AS not_meeting
FROM (SELECT t0, lag(t1) OVER (PARTITION BY mmsi, seq ORDER BY t0, t1) AS prev_t1 FROM seg)
WHERE prev_t1 IS NOT NULL;

-- 4. Every stationary segment lasts at least 300 s, and no two segments of a vessel overlap
-- beyond their shared instant.
SELECT count(*) AS stationary,
  count(*) FILTER (WHERE epoch(t1) - epoch(t0) < 300) AS shorter_than_300s
FROM seg WHERE segment_type = 'Stationary';
SELECT count(*) AS pairs, count(*) FILTER (WHERE t0 < prev_t1) AS overlapping
FROM (SELECT t0, lag(t1) OVER (PARTITION BY mmsi ORDER BY t0, t1) AS prev_t1 FROM seg)
WHERE prev_t1 IS NOT NULL;
