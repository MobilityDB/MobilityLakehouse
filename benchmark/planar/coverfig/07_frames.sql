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
INSERT INTO FigFrame VALUES
  ('trip_res8',   8, 'trip_res9',   ST_MakeEnvelope(591700, 6364383, 603700, 6373383, 25832)),
  ('trip_res9',   9, NULL,          ST_MakeEnvelope(598300, 6368430, 602300, 6371430, 25832)),
  ('region_res8', 8, 'region_res9', ST_MakeEnvelope(580400, 6365150, 604400, 6383150, 25832)),
  ('region_res9', 9, NULL,          ST_MakeEnvelope(594700, 6370300, 599900, 6374200, 25832));

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
