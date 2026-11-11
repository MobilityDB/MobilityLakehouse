-- The frame of each figure and the excerpt outlines, in ETRS89 / UTM 32N (EPSG:25832, the
-- rendering CRS), each with a 4:3 aspect, and the H3 grid of each figure's resolution over its
-- frame. make_jobs.py reads the envelopes below to place the QGIS renders, so a frame changed here
-- moves the figure without any other edit.
--
-- A frame is transformed to lon/lat with ST_Segmentize first: a rectangle in a projected CRS has
-- curved sides in geographic coordinates, and transforming its four corners alone would cut cells
-- the render shows.
DROP TABLE IF EXISTS FigFrame;
CREATE TABLE FigFrame (Fig text PRIMARY KEY, Res integer, Excerpt text,
  Geom geometry(Polygon, 25832));
-- The trip's frames follow the trip 05_trip_cells.sql picks: the coarse frame holds its whole track
-- with a margin of 1 km, widened to 4:3, and the fine frame, the excerpt, is a 4 km by 3 km
-- window centred on the cells of resolution 9 the traversal adds (class ii), or on the track's
-- middle where it adds none. Both are rounded outward to 100 m.
WITH T AS (
  SELECT ST_Transform(trajectory(Trip), 25832) AS G FROM FigTrip),
E AS (
  SELECT ST_XMin(G) - 1000 AS X0, ST_YMin(G) - 1000 AS Y0, ST_XMax(G) + 1000 AS X1,
    ST_YMax(G) + 1000 AS Y1 FROM T),
W AS (
  SELECT (X0 + X1) / 2 AS CX, (Y0 + Y1) / 2 AS CY,
    greatest(X1 - X0, (Y1 - Y0) * 4 / 3.0) AS Wd FROM E),
C AS (
  SELECT COALESCE(
    (SELECT ST_Centroid(ST_Collect(ST_Transform(Geom, 25832))) FROM FigTripCells
     WHERE Res = 9 AND Class = 'ii'),
    (SELECT ST_LineInterpolatePoint(G, 0.5) FROM T)) AS P)
INSERT INTO FigFrame
SELECT 'trip_res8', 8, 'trip_res9',
  ST_MakeEnvelope(floor((CX - Wd / 2) / 100) * 100, floor((CY - Wd * 3 / 8) / 100) * 100,
    ceil((CX + Wd / 2) / 100) * 100, ceil((CY + Wd * 3 / 8) / 100) * 100, 25832) FROM W
UNION ALL
SELECT 'trip_res9', 9, NULL,
  ST_MakeEnvelope(round(ST_X(P) / 100) * 100 - 2000, round(ST_Y(P) / 100) * 100 - 1500,
    round(ST_X(P) / 100) * 100 + 2000, round(ST_Y(P) / 100) * 100 + 1500, 25832) FROM C;
INSERT INTO FigFrame VALUES
  ('region_res8', 8, 'region_res9', ST_MakeEnvelope(659849, 6225412, 676249, 6237712, 25832)),
  ('region_res9', 9, NULL,          ST_MakeEnvelope(666322, 6230123, 669122, 6232223, 25832));

-- The H3 grid over each frame: every cell whose boundary meets the frame
DROP TABLE IF EXISTS FigGrid;
CREATE TABLE FigGrid AS
WITH F AS (
  SELECT Fig, Res, ST_Transform(ST_Segmentize(Geom, 100), 4326) AS Frame FROM FigFrame)
SELECT F.Fig, c AS Cell, cellToBoundary(c) AS Geom
FROM F, unnest(gridDisk(geoToH3IndexSet(F.Frame, F.Res), 1)) c
WHERE ST_Intersects(cellToBoundary(c), F.Frame);

SELECT f.Fig, f.Res, ST_XMin(f.Geom), ST_YMin(f.Geom), ST_XMax(f.Geom), ST_YMax(f.Geom),
  (ST_XMax(f.Geom) - ST_XMin(f.Geom)) / (ST_YMax(f.Geom) - ST_YMin(f.Geom)) AS Aspect,
  ST_AsText(ST_SnapToGrid(ST_Envelope(ST_Transform(ST_Segmentize(f.Geom, 100), 4326)), 0.0001))
    AS LonLat,
  (SELECT count(*) FROM FigGrid g WHERE g.Fig = f.Fig) AS GridCells
FROM FigFrame f ORDER BY f.Fig;

-- Cells of each class visible in each frame. These are the counts the figure captions state: a
-- finer figure is an excerpt, so its cells in frame are fewer than the cells of the whole cover,
-- and quoting the whole cover for an excerpt would overstate what the reader is looking at.
SELECT f.Fig, c.Class, count(*) AS Cells
FROM FigFrame f JOIN FigTripCells c
  ON f.Fig LIKE 'trip%' AND c.Res = f.Res
 AND ST_Intersects(c.Geom, ST_Transform(ST_Segmentize(f.Geom, 100), 4326))
GROUP BY f.Fig, c.Class
UNION ALL
SELECT f.Fig, c.Class, count(*)
FROM FigFrame f JOIN FigRegionCells c
  ON f.Fig LIKE 'region%' AND c.Res = f.Res
 AND ST_Intersects(c.Geom, ST_Transform(ST_Segmentize(f.Geom, 100), 4326))
GROUP BY f.Fig, c.Class
ORDER BY 1, 2;

-- The whole cover of each class, beside the in-frame counts above
SELECT 'trip' AS Fig, Res, Class, count(*) AS Cells FROM FigTripCells GROUP BY Res, Class
UNION ALL
SELECT 'region', Res, Class, count(*) FROM FigRegionCells WHERE InCover GROUP BY Res, Class
ORDER BY 1, 2, 3;
