-- Q4.9 collision -- region form.
--
-- Same query as queries/iceberg/collision.sql, with the region bound as a parameter and the
-- exact predicate corrected to test the region itself:
--
--   stage 1 (prune)  the region's BOUNDING BOX against the scalar sidecar columns
--                    xmin/xmax/ymin/ymax and tmin/tmax -- what a catalog can push down.
--   stage 2 (exact)  the region POLYGON, via atGeometry.
--
-- The original form used atStbox(traj, stbox(envelope, span)) for stage 2, which clips to
-- the region's BOX. For the rectangular alert belt the box is the region and the two forms
-- agree; for any other region they do not. See setup.sql in this folder.
--
-- Bind before running: region_geom, rx0/rx1/ry0/ry1.

CREATE OR REPLACE TEMP TABLE mt AS
SELECT mmsi, ship_type, traj FROM trips
WHERE dt BETWEEN getvariable('d0') AND getvariable('d1')
  AND tmax >= getvariable('t0') AND tmin <= getvariable('t1')
  AND xmax >= getvariable('rx0') AND xmin <= getvariable('rx1')
  AND ymax >= getvariable('ry0') AND ymin <= getvariable('ry1');

WITH clipped AS (
    SELECT mmsi, ship_type, g FROM (
        SELECT mmsi, ship_type,
               atTime(atGeometry(tgeompointFromEWKB(traj), (SELECT g FROM region_geom)), span(getvariable('t0'), getvariable('t1'), true, true)) AS g
        FROM mt
    ) WHERE g IS NOT NULL
),
ext AS (SELECT mmsi, g, startTimestamp(g) ts0, endTimestamp(g) ts1, ST_XMin(e) x0, ST_XMax(e) x1, ST_YMin(e) y0, ST_YMax(e) y1
        FROM (SELECT mmsi, g, ST_Extent(trajectory(g)) e FROM clipped) WHERE e IS NOT NULL),
cand AS (SELECT a.mmsi m1, b.mmsi m2, a.g t1, b.g t2 FROM ext a JOIN ext b
         ON a.mmsi < b.mmsi
        AND a.ts0 <= b.ts1 AND a.ts1 >= b.ts0   -- temporal overlap: vessels approach only at the same time
        AND a.x0 <= b.x1 + 300 AND b.x0 <= a.x1 + 300
        AND a.y0 <= b.y1 + 300 AND b.y0 <= a.y1 + 300)
SELECT count(DISTINCT (m1, m2)) AS n_pairs_300m FROM cand
WHERE nearestApproachDistance(t1, t2) < 300;
