-- Query 10, the value: the temporal count of the vessels in the belt during the window, the
-- pieces of a vessel merged first.
SELECT tCount(g)::VARCHAR FROM (SELECT MMSI, mergeAgg(g) AS g FROM clipped GROUP BY MMSI);
