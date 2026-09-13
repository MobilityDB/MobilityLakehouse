-- Query 7, position interpolation: how many vessels had a defined position in the belt at the
-- window's middle instant?
SELECT count(DISTINCT mmsi) AS n_at_t
FROM (SELECT mmsi, valueAtTimestamp(g, :tmidz) AS p FROM clipped) s
WHERE p IS NOT NULL;
