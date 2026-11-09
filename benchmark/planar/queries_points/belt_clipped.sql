-- The belt queries' legs over the point table: each vessel's positions in time order, a leg
-- joining two consecutive positions at most 600 s apart, the gap at which the cleaning ends a
-- segment; the vessel is taken to move in a straight line at constant speed along a leg, as the
-- trajectory form interpolates between its instants.
WITH legs AS (
  SELECT * FROM (
    SELECT MMSI, ship_type, t AS ta, x AS xa, y AS ya,
      LEAD(t) OVER w AS tb, LEAD(x) OVER w AS xb, LEAD(y) OVER w AS yb
    FROM mp WINDOW w AS (PARTITION BY MMSI ORDER BY t))
  WHERE tb IS NOT NULL AND tb - ta <= INTERVAL 600 SECOND)
