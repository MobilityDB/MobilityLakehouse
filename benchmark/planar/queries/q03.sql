-- Query 3, clip to region: how many distinct vessels were present in the belt during the window?
SELECT COUNT(DISTINCT MMSI) AS n_in_region FROM clipped;
