-- Query 5, bounding box: on average, how large an area did each vessel's path cover in the belt?
SELECT round((avg((x1 - x0) * (y1 - y0) / 1e6))::numeric, 2) AS avg_bbox_km2
FROM (SELECT mmsi, min(x0) x0, max(x1) x1, min(y0) y0, max(y1) y1
  FROM (SELECT mmsi, ST_XMin(trajectory(g)) x0, ST_XMax(trajectory(g)) x1,
    ST_YMin(trajectory(g)) y0, ST_YMax(trajectory(g)) y1 FROM clipped) s GROUP BY mmsi) t;
