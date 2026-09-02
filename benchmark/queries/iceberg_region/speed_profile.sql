-- Q4.6 speed_profile -- region form.
--
-- Same query as queries/iceberg/speed_profile.sql, with the region bound as a parameter and the
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
)
SELECT round(median(vmax) * 1.94384, 1) AS median_max_kn FROM (
    SELECT mmsi, max(maxValue(speed(g))) AS vmax FROM clipped GROUP BY mmsi
);
