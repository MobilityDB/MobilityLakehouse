-- The belt queries' first step: the scalar-bound prune into the table mt.
CREATE OR REPLACE TEMP TABLE mt AS
  SELECT MMSI, ship_type, trip FROM trips
  WHERE dt BETWEEN :d0 AND :d1 AND trip_tspan.tmax >= :t0 AND trip_tspan.tmin <= :t1
    AND trip_bbox.xmax >= :rxmin AND trip_bbox.xmin <= :rxmax
    AND trip_bbox.ymax >= :rymin AND trip_bbox.ymin <= :rymax;
