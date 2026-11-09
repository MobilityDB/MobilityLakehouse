-- Query 6, the values: the top speed of each vessel in the belt, in metres per second.
SELECT MMSI, MAX(maxValue(speed(g))) FROM clipped GROUP BY MMSI ORDER BY 1;
