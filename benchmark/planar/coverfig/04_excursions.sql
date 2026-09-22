-- Excursions out of Frederikshavn harbour: from the last position a vessel holds at rest inside
-- the harbour to the first position at rest there again, within four hours. Each is built as a
-- tgeompoint in SRID 4326 and scored by the cells its exact traversal adds to its per-instant
-- cover, at H3 resolutions 8 and 9.
--
-- The score is what picks the track the figure draws: a figure is worth drawing only where the
-- traversal adds cells the recorded positions miss, which is the whole point the figure makes.
SET TIMEZONE TO 'UTC';

DROP TABLE IF EXISTS Excursion CASCADE;
CREATE TABLE Excursion AS
WITH Rest AS (
  SELECT a.MMSI, a.T,
    lag(a.T) OVER (PARTITION BY a.MMSI ORDER BY a.T) AS PrevT,
    lead(a.T) OVER (PARTITION BY a.MMSI ORDER BY a.T) AS NextT
  FROM AISClean a
  WHERE a.SOG < 0.5 AND a.Geom && ST_MakeEnvelope(10.525, 57.425, 10.565, 57.447, 4326)),
Leave AS (SELECT MMSI, T, NextT FROM Rest WHERE NextT > T + interval '20 min')
SELECT row_number() OVER (ORDER BY l.MMSI, l.T) AS Id, l.MMSI, l.T AS T0, l.NextT AS T1,
  tgeompointSeq(array_agg(tgeompoint(a.Geom, a.T) ORDER BY a.T)) AS Trip
FROM Leave l JOIN AISClean a ON a.MMSI = l.MMSI AND a.T BETWEEN l.T AND l.NextT
WHERE l.NextT <= l.T + interval '4 hours'
GROUP BY l.MMSI, l.T, l.NextT;

DROP TABLE IF EXISTS ExcursionScore;
CREATE TABLE ExcursionScore AS
SELECT e.Id, e.MMSI, e.T0, e.T1, numInstants(e.Trip) AS NInst,
  round(length(transform(e.Trip, 25832))::numeric) AS LenM,
  (SELECT max(getTimestamp(i2) - getTimestamp(i1)) FROM unnest(segments(e.Trip)) s,
     LATERAL (SELECT startInstant(s) i1, endInstant(s) i2) x) AS MaxGap,
  numValues(getValues(th3index(e.Trip, 8))) AS Trav8,
  (SELECT count(DISTINCT latLngToCell(getValue(i), 8)) FROM unnest(instants(e.Trip)) i) AS Inst8,
  numValues(getValues(th3index(e.Trip, 9))) AS Trav9,
  (SELECT count(DISTINCT latLngToCell(getValue(i), 9)) FROM unnest(instants(e.Trip)) i) AS Inst9,
  stbox(e.Trip) AS Box
FROM Excursion e;

-- The candidates the figure's track is chosen from, by the cells the traversal adds
SELECT Id, MMSI, T0, NInst, LenM, Trav8 - Inst8 AS Added8, Trav9 - Inst9 AS Added9
FROM ExcursionScore ORDER BY Trav9 - Inst9 DESC, Trav8 - Inst8 DESC LIMIT 10;
