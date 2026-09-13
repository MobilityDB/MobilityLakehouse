-- Query 1, the two-port co-visit: how many vessels called at both the Rodby and Puttgarden ferry
-- ports within the window?
WITH porta AS (
  SELECT DISTINCT mmsi FROM trips
  WHERE trip_xmin <= :axmax AND trip_xmax >= :axmin
    AND trip_ymin <= :aymax AND trip_ymax >= :aymin
    AND trip_tmin <= :t1 AND trip_tmax >= :t0 AND dt BETWEEN :d0 AND :d1
    AND atStbox(tgeompointFromEWKB(trip),
      'SRID=:srid;STBOX XT(((:axmin,:aymin),(:axmax,:aymax)),[:t0s,:t1s])'::stbox) IS NOT NULL),
portb AS (
  SELECT DISTINCT mmsi FROM trips
  WHERE trip_xmin <= :bxmax AND trip_xmax >= :bxmin
    AND trip_ymin <= :bymax AND trip_ymax >= :bymin
    AND trip_tmin <= :t1 AND trip_tmax >= :t0 AND dt BETWEEN :d0 AND :d1
    AND atStbox(tgeompointFromEWKB(trip),
      'SRID=:srid;STBOX XT(((:bxmin,:bymin),(:bxmax,:bymax)),[:t0s,:t1s])'::stbox) IS NOT NULL)
SELECT count(*) AS n_both FROM porta a JOIN portb b USING (mmsi);
