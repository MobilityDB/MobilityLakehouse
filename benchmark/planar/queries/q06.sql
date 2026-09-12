-- Query 6, speed profile: the median top speed, in knots, among the vessels in the belt.
SELECT round(median(vmax) * 1.94384, 1) AS median_max_kn
FROM (SELECT MMSI, MAX(maxValue(speed(g))) AS vmax FROM clipped GROUP BY MMSI);
