-- Query 7, the values: the position of each vessel present at the instant, the least and the
-- greatest coordinate over its pieces, which agree when a vessel holds one position.
SELECT MMSI, MIN(ST_X(p)), MAX(ST_X(p)), MIN(ST_Y(p)), MAX(ST_Y(p))
FROM (SELECT MMSI, valueAtTimestamp(g, :tmidz) AS p FROM clipped)
WHERE p IS NOT NULL GROUP BY MMSI ORDER BY 1;
