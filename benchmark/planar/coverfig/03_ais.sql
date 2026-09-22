-- Load the pre-filtered AIS positions around Frederikshavn and clean them minimally: one position
-- per vessel and timestamp, vessels only, and no impossible jump.
--
-- The cleaning here is the figures' own and is deliberately simpler than the benchmark's
-- (planar/20_clean.sql): a figure shows one track over a few hours, so the reachability walk the
-- corpus needs would change nothing visible while requiring the whole month's machinery.
--
-- Takes :ais_csv, written by 01_prefilter_ais.sh.
SET TIMEZONE TO 'UTC';

DROP TABLE IF EXISTS AISRaw;
CREATE TABLE AISRaw (T timestamp, Mobile text, MMSI bigint, Lat float, Lon float,
  SOG float, ShipType text, Name text);
COPY AISRaw FROM :'ais_csv' WITH (FORMAT csv, HEADER true);
SELECT T::date AS day, count(*) FROM AISRaw GROUP BY 1 ORDER BY 1;

-- One position per vessel and timestamp; vessels only (a 9-digit MMSI whose MID is 2xx-7xx),
-- positions inside the valid lon/lat range
DROP TABLE IF EXISTS AISClean;
CREATE TABLE AISClean AS
SELECT DISTINCT ON (MMSI, T) MMSI, T, Mobile, SOG, ShipType, Name,
  ST_SetSRID(ST_Point(Lon, Lat), 4326) AS Geom
FROM AISRaw
WHERE Mobile IN ('Class A', 'Class B') AND MMSI BETWEEN 200000000 AND 799999999
  AND Lat BETWEEN -90 AND 90 AND Lon BETWEEN -180 AND 180
ORDER BY MMSI, T;

-- Drop an impossible jump: a position reached from the previous one and left for the next one
-- faster than 50 knots (25.7 m/s), a spike off the track. Requiring BOTH steps to exceed the cap
-- is what distinguishes a spike from the position that returns to the track after one.
DELETE FROM AISClean a USING (
  SELECT MMSI, T,
    ST_DistanceSphere(Geom, lag(Geom) OVER w) /
      GREATEST(EXTRACT(EPOCH FROM T - lag(T) OVER w), 1) AS speedin,
    ST_DistanceSphere(Geom, lead(Geom) OVER w) /
      GREATEST(EXTRACT(EPOCH FROM lead(T) OVER w - T), 1) AS speedout
  FROM AISClean WINDOW w AS (PARTITION BY MMSI ORDER BY T)) j
WHERE a.MMSI = j.MMSI AND a.T = j.T AND j.speedin > 25.7 AND j.speedout > 25.7;
CREATE INDEX ON AISClean (MMSI, T);
SELECT count(*) AS clean, count(DISTINCT MMSI) AS vessels FROM AISClean;
