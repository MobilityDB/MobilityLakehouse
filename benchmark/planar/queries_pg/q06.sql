-- Query 6, speed profile: the median top speed, in knots, among the vessels in the belt.
SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY vmax) * 1.94384)::numeric, 1)
  AS median_max_kn
FROM (SELECT mmsi, max(maxValue(speed(g))) AS vmax FROM clipped GROUP BY mmsi) s;
