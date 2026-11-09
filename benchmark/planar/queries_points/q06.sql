-- Query 6 over the point table, speed profile: the median top speed, in knots, among the vessels
-- in the belt, a leg's speed its length over its duration.
SELECT round(median(vmax) * 1.94384, 1) AS median_max_kn
FROM (SELECT MMSI, MAX(sqrt((xb - xa) ^ 2 + (yb - ya) ^ 2) / epoch(tb - ta)) AS vmax
  FROM legs GROUP BY MMSI);
