-- Query 6, speed profile: the median top speed, in knots, among the vessels in the belt.
SELECT CAST(median(vmax) * 1.94384 AS DECIMAL(20, 1)) AS median_max_kn
FROM (SELECT mmsi, max(maxValue(speed(g))) AS vmax FROM clipped GROUP BY mmsi) s;
