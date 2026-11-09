-- Query 5, the values: the box of each vessel's path in the belt during the window.
SELECT MMSI, MIN(ST_XMin(trajectory(g))), MAX(ST_XMax(trajectory(g))),
  MIN(ST_YMin(trajectory(g))), MAX(ST_YMax(trajectory(g)))
FROM clipped GROUP BY MMSI ORDER BY 1;
