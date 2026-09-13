-- The belt queries' first step: the scalar-bound prune into the view mt.
CREATE OR REPLACE TEMPORARY VIEW mt AS
  SELECT mmsi, ship_type, trip FROM trips
  WHERE dt BETWEEN :d0 AND :d1 AND trip_tmax >= :t0 AND trip_tmin <= :t1
    AND trip_xmax >= :rxmin AND trip_xmin <= :rxmax
    AND trip_ymax >= :rymin AND trip_ymin <= :rymax;
