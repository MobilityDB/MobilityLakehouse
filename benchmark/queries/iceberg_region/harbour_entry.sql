-- Q4.2 harbour_entry -- region form.
--
-- Same query as queries/iceberg/harbour_entry.sql, with the region bound as a parameter and the
-- exact predicate corrected to test the region itself:
--
--   stage 1 (prune)  the region's BOUNDING BOX against the scalar sidecar columns
--                    xmin/xmax/ymin/ymax and tmin/tmax -- what a catalog can push down.
--   stage 2 (exact)  the region POLYGON, via eIntersects.
--
-- The original form used atStbox(traj, stbox(envelope, span)) for stage 2, which clips to
-- the region's BOX. For the rectangular alert belt the box is the region and the two forms
-- agree; for any other region they do not. See setup.sql in this folder.
--
-- Bind before running: region_geom, rx0/rx1/ry0/ry1.

WITH port AS (
    SELECT (SELECT g FROM region_geom) AS g
),
cand AS (
    SELECT mmsi,
           atTime(tgeompointFromEWKB(traj),
                  span(getvariable('t0'),
                       getvariable('t1'), true, false)) AS trip
    FROM trips
    WHERE xmin <= getvariable('rx1') AND xmax >= getvariable('rx0')
      AND ymin <= getvariable('ry1') AND ymax >= getvariable('ry0')
      AND tmin <= getvariable('t1') AND tmax >= getvariable('t0')
      AND dt BETWEEN getvariable('d0') AND getvariable('d1')
)
SELECT COUNT(DISTINCT mmsi) AS n_vessels
FROM cand, port
WHERE trip IS NOT NULL AND eIntersects(trip, port.g)
;
