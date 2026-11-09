-- Query 5 over the point table, bounding box: the mean area, in square kilometres, of the box of
-- each vessel's positions in the belt during the window.
SELECT round(AVG((x1 - x0) * (y1 - y0) / 1e6), 2) AS avg_bbox_km2
FROM (SELECT MMSI, MIN(x) x0, MAX(x) x1, MIN(y) y0, MAX(y) y1 FROM mp GROUP BY MMSI);
