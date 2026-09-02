-- Q4.5 bounding_box -- region form.
--
-- Same query as queries/iceberg/bounding_box.sql, with the region bound as a parameter and the
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
SELECT round(avg((x1 - x0) * (y1 - y0) / 1e6), 2) AS avg_bbox_km2 FROM (
    SELECT mmsi, min(x0) x0, max(x1) x1, min(y0) y0, max(y1) y1 FROM (
        SELECT mmsi, ST_XMin(trajectory(g)) x0, ST_XMax(trajectory(g)) x1,
               ST_YMin(trajectory(g)) y0, ST_YMax(trajectory(g)) y1 FROM clipped
    ) GROUP BY mmsi
);
