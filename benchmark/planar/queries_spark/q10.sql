-- Query 10, peak occupancy: how many vessels were in the belt at the same time, at most, during
-- the window? The pieces of a vessel are merged into one trajectory first, so the temporal count
-- counts vessels and not the pieces a layout splits them into.
SELECT tint_max_value(tCount(g)) AS peak_vessels
FROM (SELECT mmsi, mergeAgg(g) AS g FROM clipped GROUP BY mmsi) s;
