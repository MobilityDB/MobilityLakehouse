-- Query 10, peak occupancy: how many vessels were in the belt at the same time, at most, during
-- the window? The pieces of a vessel are merged into one trajectory first, so the temporal count
-- counts vessels and not the pieces a layout splits them into.
SELECT maxValue(tCount(g)) AS peak_vessels
FROM (SELECT MMSI, mergeAgg(g) AS g FROM clipped GROUP BY MMSI);
