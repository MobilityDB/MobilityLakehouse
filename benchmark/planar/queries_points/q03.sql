-- Query 3 over the point table, region clip: how many distinct vessels reported a position in
-- the belt during the window?
SELECT COUNT(DISTINCT MMSI) AS n_in_region FROM mp;
