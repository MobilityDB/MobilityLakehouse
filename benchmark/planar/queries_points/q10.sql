-- Query 10 over the point table, peak occupancy: how many vessels were in the belt at the same
-- time, at most, during the window? A vessel is present from the first to the last of a run of
-- its positions at most 600 s apart, and the presences are swept in time order, an arrival
-- counted before a departure at the same instant.
, brk AS (
  SELECT MMSI, t, CASE WHEN t - LAG(t) OVER (PARTITION BY MMSI ORDER BY t) <= INTERVAL 600 SECOND
                       THEN 0 ELSE 1 END AS b
  FROM mp),
isl AS (
  SELECT MMSI, MIN(t) AS s, MAX(t) AS e FROM (
    SELECT MMSI, t, SUM(b) OVER (PARTITION BY MMSI ORDER BY t) AS g FROM brk)
  GROUP BY MMSI, g)
SELECT MAX(n) AS peak_vessels FROM (
  SELECT SUM(d) OVER (ORDER BY ts, d DESC ROWS UNBOUNDED PRECEDING) AS n FROM (
    SELECT s AS ts, 1 AS d FROM isl UNION ALL SELECT e AS ts, -1 AS d FROM isl));
