-- The belt queries' second step: each retained trajectory clipped to the belt and the window.
WITH clipped AS (
  SELECT MMSI, ship_type, g FROM (
    SELECT MMSI, ship_type, atStbox(tgeompointFromEWKB(trip),
      stbox('SRID=:srid;STBOX XT(((:rxmin,:rymin),(:rxmax,:rymax)),[:t0s,:t1s])')) AS g
    FROM mt) WHERE g IS NOT NULL)
