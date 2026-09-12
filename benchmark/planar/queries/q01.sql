-- Query 1, the two-port co-visit: how many vessels called at both the Rodby and Puttgarden ferry
-- ports within the window?
WITH PortA AS (
  SELECT DISTINCT MMSI FROM trips
  WHERE trip_bbox.xmin <= :axmax AND trip_bbox.xmax >= :axmin
    AND trip_bbox.ymin <= :aymax AND trip_bbox.ymax >= :aymin
    AND trip_tspan.tmin <= :t1 AND trip_tspan.tmax >= :t0 AND dt BETWEEN :d0 AND :d1
    AND atStbox(tgeompointFromEWKB(trip),
      stbox('SRID=:srid;STBOX XT(((:axmin,:aymin),(:axmax,:aymax)),[:t0s,:t1s])')) IS NOT NULL),
PortB AS (
  SELECT DISTINCT MMSI FROM trips
  WHERE trip_bbox.xmin <= :bxmax AND trip_bbox.xmax >= :bxmin
    AND trip_bbox.ymin <= :bymax AND trip_bbox.ymax >= :bymin
    AND trip_tspan.tmin <= :t1 AND trip_tspan.tmax >= :t0 AND dt BETWEEN :d0 AND :d1
    AND atStbox(tgeompointFromEWKB(trip),
      stbox('SRID=:srid;STBOX XT(((:bxmin,:bymin),(:bxmax,:bymax)),[:t0s,:t1s])')) IS NOT NULL)
SELECT COUNT(*) AS n_both FROM PortA a JOIN PortB b USING (MMSI);
