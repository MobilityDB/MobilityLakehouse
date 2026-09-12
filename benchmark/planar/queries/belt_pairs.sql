-- The proximity queries' candidate pairs: vessels overlapping in time whose clipped extents lie
-- within :gate metres of each other.
, Ext AS (
  SELECT MMSI, g, startTimestamp(g) ts0, endTimestamp(g) ts1, ST_XMin(e) x0,
    ST_XMax(e) x1, ST_YMin(e) y0, ST_YMax(e) y1
  FROM (SELECT MMSI, g, ST_Extent(trajectory(g)) e FROM clipped) WHERE e IS NOT NULL),
Cand AS (
  SELECT a.MMSI m1, b.MMSI m2, a.g t1, b.g t2 FROM Ext a JOIN Ext b
  ON a.MMSI < b.MMSI AND a.ts0 <= b.ts1 AND a.ts1 >= b.ts0
    AND a.x0 <= b.x1 + :gate AND b.x0 <= a.x1 + :gate
    AND a.y0 <= b.y1 + :gate AND b.y0 <= a.y1 + :gate)
