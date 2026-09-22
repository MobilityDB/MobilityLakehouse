-- The H3 cover of one real protected-area polygon, at resolutions 8 and 9: the exact cover
-- geoToH3IndexSet(polygon, res), the cells the grid assigns a point of the polygon, in two classes:
--   i   interior cells: cells of the cover no point of any ring, outer or hole, is assigned to,
--       the cover minus the ring cells;
--   ii  boundary cells: the cells the rings pass through, the union of geoToH3IndexSet over the
--       rings as linestrings, holes included.
-- The two together are the conservative rasterization the region cover is: the rings walked cell
-- to cell, and the components they enclose filled.
--
-- Requires 02_natural_areas.sql.

-- The polygon the figures draw is the first part of the marine protected area the WHERE clause
-- below identifies, a site with five islands as holes, which is what makes the hole-aware fill
-- visible rather than incidental.
DROP TABLE IF EXISTS FigRegion;
CREATE TABLE FigRegion AS
SELECT n.Id, n.SiteId, n.NameEng, n.DesigEng, d.path[1] AS Part, d.Geom
FROM NaturalAreas n, ST_Dump(n.GeomLL) d
WHERE n.Id = 17 AND d.path[1] = 1;

DROP TABLE IF EXISTS FigRegionCells;
CREATE TABLE FigRegionCells AS
WITH Res(Res) AS (VALUES (8), (9)),
Cover AS (
  SELECT r.Res, x AS Cell
  FROM FigRegion p, Res r, unnest(getValues(geoToH3IndexSet(p.Geom, r.Res))) x),
Rings AS (
  SELECT r.Res, ST_ExteriorRing(d.geom) AS Ring
  FROM FigRegion p, Res r, ST_DumpRings(p.Geom) d),
Boundary AS (
  SELECT DISTINCT g.Res, x AS Cell
  FROM Rings g, unnest(getValues(geoToH3IndexSet(g.Ring, g.Res))) x)
SELECT COALESCE(c.Res, b.Res) AS Res, COALESCE(c.Cell, b.Cell) AS Cell,
  c.Cell IS NOT NULL AS InCover, b.Cell IS NOT NULL AS OnRing,
  CASE WHEN b.Cell IS NOT NULL THEN 'ii' ELSE 'i' END AS Class,
  cellToBoundary(COALESCE(c.Cell, b.Cell)) AS Geom
FROM Cover c FULL JOIN Boundary b ON c.Res = b.Res AND c.Cell = b.Cell;

-- The classes, the cover, and the checks the cover must pass: every ring cell lies in the cover,
-- the two classes partition it, and every cover cell meets the polygon. RingCellsOutsideCover
-- reading anything but 0 means the cover omits a cell its own boundary passes through, which is
-- the failure the construction exists to rule out.
SELECT f.Res,
  count(*) FILTER (WHERE Class = 'i') AS Interior,
  count(*) FILTER (WHERE Class = 'ii') AS Boundary,
  count(*) FILTER (WHERE InCover) AS Cover,
  count(*) FILTER (WHERE Class = 'i') + count(*) FILTER (WHERE Class = 'ii')
    = count(*) FILTER (WHERE InCover) AS ClassesSumToCover,
  count(*) FILTER (WHERE Class = 'i' AND OnRing) AS InteriorOnRing,
  count(*) FILTER (WHERE OnRing AND NOT InCover) AS RingCellsOutsideCover,
  count(*) FILTER (WHERE NOT ST_Intersects(f.Geom, p.Geom)) AS CellsNotMeetingPolygon
FROM FigRegionCells f, FigRegion p GROUP BY f.Res ORDER BY f.Res;
SELECT Id, SiteId, NameEng, DesigEng, Part, ST_NumInteriorRings(Geom) AS Holes,
  round((ST_Area(Geom::geography) / 1e6)::numeric, 2) AS AreaKm2
FROM FigRegion;
