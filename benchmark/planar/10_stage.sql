-- Stage 1 of the planar pipeline: the raw reports of [lo, hi) that pass the field checks of the
-- lakehouse paper, each projected to EPSG:25832 with its WGS84 longitude and latitude kept, and
-- tagged with the vessel bucket that stage 2 reads whole. The COPY that writes the buckets is
-- appended by run_clean.sh, because COPY ... TO takes a literal path.
--
-- A vessel identifier is kept when it is a ship-station MMSI (ITU-R M.585): nine digits whose
-- first three are Maritime Identification Digits allocated to an administration. The allocated
-- MIDs are itu_mid.csv, 292 codes taken from the MID mapping of github.com/michaeljfazio/MIDs and
-- equal as a set to the table of the English Wikipedia article "Maritime identification digits",
-- both transcribing the ITU Table of Maritime Identification Digits.
--
-- Variables: raw_glob, lo, hi (timestamps), nbuckets, mid_csv (the MID table).

CREATE OR REPLACE TEMP TABLE staged AS
SELECT T AS t, MMSI AS mmsi, Latitude AS lat, Longitude AS lon, SOG AS sog,
  NavigationalStatus AS nav, ShipType AS ship_type,
  ST_X(g) AS x, ST_Y(g) AS y,
  (hash(MMSI) % getvariable('nbuckets'))::INTEGER AS bucket
FROM (
  SELECT *,
    ST_Transform(ST_Point(Longitude, Latitude), 'EPSG:4326', 'EPSG:25832', always_xy := true) AS g
  FROM read_parquet(getvariable('raw_glob'))
  WHERE T >= getvariable('lo')::TIMESTAMP AND T < getvariable('hi')::TIMESTAMP
    AND TypeOfMobile = 'Class A'
    AND MMSI BETWEEN 200000000 AND 799999999
    AND MMSI // 1000000 IN (SELECT mid FROM read_csv(getvariable('mid_csv')))
    -- The study area, where EPSG:25832 applies.
    AND Longitude BETWEEN 3 AND 16 AND Latitude BETWEEN 53 AND 59
    AND (Draught IS NULL OR Draught <= 28.5)
    AND (Width IS NULL OR Width <= 75)
    AND (Length IS NULL OR Length <= 488));
