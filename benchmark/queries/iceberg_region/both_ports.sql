-- Q4.1 both_ports -- region form.
--
-- Same query as queries/iceberg/both_ports.sql, with the region bound as a parameter and the
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
-- Bind before running: region_geom, region_geom_b, rx0/rx1/ry0/ry1, sx0/sx1/sy0/sy1.

WITH rodby AS (
    SELECT DISTINCT mmsi
    FROM trips
    WHERE xmin <= getvariable('rx1') AND xmax >= getvariable('rx0')
      AND ymin <= getvariable('ry1') AND ymax >= getvariable('ry0')
      AND tmin <= getvariable('t1') AND tmax >= getvariable('t0')
      AND dt BETWEEN getvariable('d0') AND getvariable('d1')
      AND atTime(atGeometry(tgeompointFromEWKB(traj), (SELECT g FROM region_geom)), span(getvariable('t0'), getvariable('t1'), true, true)) IS NOT NULL
),
puttgarden AS (
    SELECT DISTINCT mmsi
    FROM trips
    WHERE xmin <= getvariable('sx1') AND xmax >= getvariable('sx0')
      AND ymin <= getvariable('sy1') AND ymax >= getvariable('sy0')
      AND tmin <= getvariable('t1') AND tmax >= getvariable('t0')
      AND dt BETWEEN getvariable('d0') AND getvariable('d1')
      AND atTime(atGeometry(tgeompointFromEWKB(traj), (SELECT g FROM region_geom_b)), span(getvariable('t0'), getvariable('t1'), true, true)) IS NOT NULL
)
SELECT COUNT(*) AS n_both
FROM rodby r JOIN puttgarden pg USING (mmsi);
