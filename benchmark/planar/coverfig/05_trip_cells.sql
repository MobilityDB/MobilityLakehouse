-- The H3 cover of one real vessel track leaving Frederikshavn, at resolutions 8 and 9, in two
-- classes:
--   i   cells holding at least one recorded position, latLngToCell of each instant: the
--       per-instant cover, which is what a grid records when it records where samples fall;
--   ii  cells the exact cell-to-cell traversal th3index(tgeompoint, integer) adds, where the
--       interpolated path crosses a cell no sample falls in.
-- Class (ii) is the figure's subject: those cells are exactly what a sampled cover loses, and a
-- query region meeting only them finds no match although the vessel passed through it.
--
-- Requires 03_ais.sql and 04_excursions.sql.
SET TIMEZONE TO 'UTC';

-- The track the figures draw is one excursion of a pilot vessel, which the WHERE clause below
-- identifies by its MMSI and its start instant: it leaves its berth in Frederikshavn and returns
-- to rest there. 04_excursions.sql ranks the candidates by the cells their traversal adds.
DROP TABLE IF EXISTS FigTrip;
CREATE TABLE FigTrip AS
SELECT MMSI, Trip FROM Excursion
WHERE MMSI = 219002732 AND T0 = '2025-01-02 12:17:04';

DROP TABLE IF EXISTS FigTripLine;
CREATE TABLE FigTripLine AS
SELECT trajectory(Trip) AS Geom FROM FigTrip;

DROP TABLE IF EXISTS FigTripSamples;
CREATE TABLE FigTripSamples AS
SELECT getTimestamp(i) AS T, getValue(i) AS Geom FROM FigTrip, unnest(instants(Trip)) i;

DROP TABLE IF EXISTS FigTripCells;
CREATE TABLE FigTripCells AS
WITH Res(Res) AS (VALUES (8), (9)),
Trav AS (
  SELECT r.Res, c AS Cell
  FROM FigTrip t, Res r, unnest(getValues(th3index(t.Trip, r.Res))) c),
Inst AS (
  SELECT DISTINCT r.Res, latLngToCell(s.Geom, r.Res) AS Cell
  FROM FigTripSamples s, Res r)
SELECT COALESCE(v.Res, i.Res) AS Res, COALESCE(v.Cell, i.Cell) AS Cell,
  CASE WHEN i.Cell IS NOT NULL THEN 'i' ELSE 'ii' END AS Class,
  v.Cell IS NOT NULL AS InTraversal,
  cellToBoundary(COALESCE(v.Cell, i.Cell)) AS Geom
FROM Trav v FULL JOIN Inst i ON v.Res = i.Res AND v.Cell = i.Cell;

-- Every per-instant cell must lie in the traversal: NotInTraversal reading anything but 0 means
-- the traversal skips a cell one of its own samples falls in.
SELECT Res, Class, count(*) AS Cells, count(*) FILTER (WHERE NOT InTraversal) AS NotInTraversal
FROM FigTripCells GROUP BY Res, Class ORDER BY Res, Class;
SELECT MMSI, startTimestamp(Trip), endTimestamp(Trip), numInstants(Trip),
  round(ST_Length(trajectory(Trip)::geography)::numeric) AS LengthM
FROM FigTrip;
