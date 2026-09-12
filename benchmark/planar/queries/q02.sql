-- Query 2, presence in a port: how many distinct vessels were present in the Port of Goteborg
-- during the window?
WITH port AS (SELECT ST_MakeEnvelope(:rxmin, :rymin, :rxmax, :rymax) AS g),
cand AS (
  SELECT MMSI, atTime(tgeompointFromEWKB(trip), span(:t0z, :t1z, true, true)) AS trip
  FROM trips
  WHERE trip_bbox.xmin <= :rxmax AND trip_bbox.xmax >= :rxmin
    AND trip_bbox.ymin <= :rymax AND trip_bbox.ymax >= :rymin
    AND trip_tspan.tmin <= :t1 AND trip_tspan.tmax >= :t0 AND dt BETWEEN :d0 AND :d1)
SELECT COUNT(DISTINCT MMSI) AS n_vessels FROM cand, port
WHERE trip IS NOT NULL AND eIntersects(trip, port.g);
