-- Query 7, position interpolation: how many vessels had a defined position in the belt at the
-- window's middle instant?
SELECT COUNT(DISTINCT MMSI) AS n_at_T
FROM (SELECT MMSI, valueAtTimestamp(g, :tmidz) AS p FROM clipped)
WHERE p IS NOT NULL;
