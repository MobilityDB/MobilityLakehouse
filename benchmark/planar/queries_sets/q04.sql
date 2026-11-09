-- Query 4, the values: the distance each vessel travels in the belt during the window, in
-- metres.
SELECT MMSI, sum(length(g)) FROM clipped GROUP BY MMSI ORDER BY 1;
