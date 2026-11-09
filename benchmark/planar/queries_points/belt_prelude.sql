-- The belt queries' first step over the point table: the positions in the belt during the
-- window, kept in the table mp, as the trajectory form keeps its pruned rows in mt.
CREATE OR REPLACE TEMP TABLE mp AS
  SELECT MMSI, ship_type, t, bbox.xmin AS x, bbox.ymin AS y FROM trips
  WHERE dt BETWEEN :d0 AND :d1 AND t BETWEEN :t0 AND :t1
    AND bbox.xmin BETWEEN :rxmin AND :rxmax AND bbox.ymin BETWEEN :rymin AND :rymax;
