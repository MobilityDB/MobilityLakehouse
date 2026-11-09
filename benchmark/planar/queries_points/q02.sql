-- Query 2 over the point table, presence in a port: how many distinct vessels reported a
-- position in the Port of Goteborg during the window?
SELECT COUNT(DISTINCT MMSI) AS n_vessels FROM trips
WHERE bbox.xmin BETWEEN :rxmin AND :rxmax AND bbox.ymin BETWEEN :rymin AND :rymax
  AND t BETWEEN :t0 AND :t1 AND dt BETWEEN :d0 AND :d1;
