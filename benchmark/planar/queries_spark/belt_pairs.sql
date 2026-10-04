-- The proximity queries' candidate pairs: vessels overlapping in time whose clipped extents lie
-- within :gate metres of each other.
, ext AS (
  SELECT mmsi, g, startTimestamp(g) ts0, endTimestamp(g) ts1,
    xMin(b) x0, xMax(b) x1, yMin(b) y0, yMax(b) y1
  FROM (SELECT mmsi, g, stbox(g) b FROM clipped) s WHERE b IS NOT NULL),
cand AS (
  SELECT a.mmsi m1, b.mmsi m2, a.g t1, b.g t2 FROM ext a JOIN ext b
  ON a.mmsi < b.mmsi AND a.ts0 <= b.ts1 AND a.ts1 >= b.ts0
    AND a.x0 <= b.x1 + :gate AND b.x0 <= a.x1 + :gate
    AND a.y0 <= b.y1 + :gate AND b.y0 <= a.y1 + :gate)
