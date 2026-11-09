-- Query 1 over the point table, co-visit: how many vessels reported a position at both ferry
-- ports, Rodby and Puttgarden, during the window?
WITH PortA AS (
  SELECT DISTINCT MMSI FROM trips
  WHERE bbox.xmin BETWEEN :axmin AND :axmax AND bbox.ymin BETWEEN :aymin AND :aymax
    AND t BETWEEN :t0 AND :t1 AND dt BETWEEN :d0 AND :d1),
PortB AS (
  SELECT DISTINCT MMSI FROM trips
  WHERE bbox.xmin BETWEEN :bxmin AND :bxmax AND bbox.ymin BETWEEN :bymin AND :bymax
    AND t BETWEEN :t0 AND :t1 AND dt BETWEEN :d0 AND :d1)
SELECT COUNT(*) AS n_both FROM PortA a JOIN PortB b USING (MMSI);
