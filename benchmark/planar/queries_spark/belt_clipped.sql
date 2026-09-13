-- The belt queries' second step: each retained trajectory clipped to the belt and the window.
WITH clipped AS (
  SELECT mmsi, ship_type, g FROM (
    SELECT mmsi, ship_type, tgeo_at_stbox(hex(trip),
      'SRID=:srid;STBOX XT(((:rxmin,:rymin),(:rxmax,:rymax)),[:t0s,:t1s])', true) AS g
    FROM mt) s WHERE g IS NOT NULL)
