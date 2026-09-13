-- Query 2, presence in a port: how many distinct vessels were present in the Port of Goteborg
-- during the window?
WITH port AS (SELECT ST_MakeEnvelope(:rxmin, :rymin, :rxmax, :rymax, :srid) AS g),
cand AS (
  SELECT mmsi, atTime(tgeompointFromEWKB(trip), span(:t0z, :t1z, true, true)) AS trip
  FROM trips
  WHERE trip_xmin <= :rxmax AND trip_xmax >= :rxmin
    AND trip_ymin <= :rymax AND trip_ymax >= :rymin
    AND trip_tmin <= :t1 AND trip_tmax >= :t0 AND dt BETWEEN :d0 AND :d1)
SELECT count(DISTINCT mmsi) AS n_vessels FROM cand, port
WHERE trip IS NOT NULL AND eIntersects(trip, port.g);
