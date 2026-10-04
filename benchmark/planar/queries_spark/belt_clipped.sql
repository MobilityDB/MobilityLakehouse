-- The belt queries' second step: each retained trajectory clipped to the belt and the window.
WITH clipped AS (
  SELECT mmsi, ship_type, g FROM (
    SELECT mmsi, ship_type, atStbox(tgeompointFromHexEWKB(hex(trip)),
      stboxFromText('SRID=:srid;STBOX XT(((:rxmin,:rymin),(:rxmax,:rymax)),[:t0s,:t1s])')) AS g
    FROM mt) s WHERE g IS NOT NULL)
