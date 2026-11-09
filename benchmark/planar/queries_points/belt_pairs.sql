-- The proximity queries' candidate pairs over the point table: legs of two vessels that overlap
-- in time and whose extents lie within :gate metres of each other, with the closest distance the
-- two linear motions reach over their shared time, from the relative position at its start and
-- the relative velocity, the time of closest approach clamped to the shared interval.
, Lv AS (
  SELECT MMSI, ta, tb, xa, ya, (xb - xa) / epoch(tb - ta) AS vx, (yb - ya) / epoch(tb - ta) AS vy,
    least(xa, xb) AS x0, greatest(xa, xb) AS x1, least(ya, yb) AS y0, greatest(ya, yb) AS y1
  FROM legs),
Rel AS (
  SELECT a.MMSI AS m1, b.MMSI AS m2,
    epoch(least(a.tb, b.tb) - greatest(a.ta, b.ta)) AS span,
    (a.xa + a.vx * epoch(greatest(a.ta, b.ta) - a.ta)) - (b.xa + b.vx * epoch(greatest(a.ta, b.ta) - b.ta)) AS dx,
    (a.ya + a.vy * epoch(greatest(a.ta, b.ta) - a.ta)) - (b.ya + b.vy * epoch(greatest(a.ta, b.ta) - b.ta)) AS dy,
    a.vx - b.vx AS wx, a.vy - b.vy AS wy
  FROM Lv a JOIN Lv b
  ON a.MMSI < b.MMSI AND a.ta <= b.tb AND a.tb >= b.ta
    AND a.x0 <= b.x1 + :gate AND b.x0 <= a.x1 + :gate
    AND a.y0 <= b.y1 + :gate AND b.y0 <= a.y1 + :gate),
Cand AS (
  SELECT m1, m2, sqrt((dx + wx * s) ^ 2 + (dy + wy * s) ^ 2) AS dist FROM (
    SELECT m1, m2, dx, dy, wx, wy,
      CASE WHEN wx * wx + wy * wy = 0 THEN 0
           ELSE least(span, greatest(0, -(dx * wx + dy * wy) / (wx * wx + wy * wy))) END AS s
    FROM Rel))
