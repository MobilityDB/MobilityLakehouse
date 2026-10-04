-- Query 5, bounding box: on average, how large an area did each vessel's path cover in the belt?
SELECT CAST(avg((x1 - x0) * (y1 - y0) / 1e6) AS DECIMAL(20, 2)) AS avg_bbox_km2
FROM (SELECT mmsi, min(x0) x0, max(x1) x1, min(y0) y0, max(y1) y1
  FROM (SELECT mmsi, xMin(b) x0, xMax(b) x1, yMin(b) y0, yMax(b) y1
    FROM (SELECT mmsi, stbox(g) b FROM clipped) s) t GROUP BY mmsi) u;
