-- Export the layers of the one-trip tiling figures, tiling_L2_trip and tiling_L3_trip: one vessel's
-- in-motion segment of the day, cut to the frame, then split as 41_part_layouts.sh splits a trip,
--   L2  spaceSplit on the region grid; a piece goes to the file of the cell spaceSplit answers;
--   L3  splitEachNStboxes(trip, ADAPTIVE_NSEG) and atStbox; a piece goes to the file of the cell
--       holding the center of its box;
-- each piece carrying the cell of its file, which the figure colours it by. The cut comes first so
-- the figure shows the pieces of the part of the trip it frames, and lies a margin inside the frame
-- so the cut ends of the trip read as cuts rather than as the frame's border.
--
-- Unlike 01_export.sql this decodes trajectories, so the engine carries MobilityDuck, which
-- 79_layout_figures.sh already requires.
--
-- Variables: out (the run), stage (where the GeoPackages go and frames.csv is), day, trip_vessel
-- (the MMSI drawn), cell (CELL_SIZE), nseg (ADAPTIVE_NSEG), margin (the cut's inset, in meters).
INSTALL spatial;
LOAD spatial;
SET geometry_always_xy = true;

CREATE OR REPLACE MACRO box(x0, y0, x1, y1) AS
  ST_GeomFromText('POLYGON((' || x0 || ' ' || y0 || ',' || x1 || ' ' || y0 || ',' ||
                  x1 || ' ' || y1 || ',' || x0 || ' ' || y1 || ',' || x0 || ' ' || y0 || '))');

-- The cut, the frame of the figure less the margin, over the whole day
CREATE OR REPLACE TABLE TripCut AS
SELECT x0 + getvariable('margin') AS x0, y0 + getvariable('margin') AS y0,
  x1 - getvariable('margin') AS x1, y1 - getvariable('margin') AS y1,
  getvariable('day') || ' 00:00:00+00' AS t0,
  strftime(cast(getvariable('day') AS DATE) + INTERVAL 1 DAY, '%Y-%m-%d') || ' 00:00:00+00' AS t1
FROM read_csv(getvariable('stage') || '/frames.csv', header = true)
WHERE fig = 'tiling_L3_trip';

CREATE OR REPLACE TABLE Trip AS
SELECT mmsi, atStbox(tgeompointFromEWKB(l.trip), stbox('SRID=25832;STBOX XT(((' ||
    c.x0 || ',' || c.y0 || '),(' || c.x1 || ',' || c.y1 || ')),[' || c.t0 || ', ' || c.t1 ||
    '])')) AS trip
FROM read_parquet(getvariable('out') || '/L0/*/*/day-' || getvariable('day') || '.parquet') l,
  TripCut c
WHERE l.segment_type = 'In motion' AND l.mmsi = getvariable('trip_vessel');

CREATE OR REPLACE TABLE TripL2 AS
SELECT t.mmsi, s.tpoint AS p,
  (floor(ST_X(s.spaceBin) / getvariable('cell')))::INTEGER AS cell_x,
  (floor(ST_Y(s.spaceBin) / getvariable('cell')))::INTEGER AS cell_y
FROM Trip t, spaceSplit(t.trip, getvariable('cell'), getvariable('cell'), getvariable('cell')) s
WHERE t.trip IS NOT NULL AND s.tpoint IS NOT NULL;

CREATE OR REPLACE TABLE TripL3 AS
WITH pieces AS (
  SELECT mmsi, unnest(list_transform(splitEachNStboxes(trip, getvariable('nseg')),
                                     lambda bx: atStbox(trip, bx))) AS p
  FROM Trip WHERE trip IS NOT NULL)
SELECT mmsi, p,
  (floor(((Xmin(stbox(p)) + Xmax(stbox(p))) / 2) / getvariable('cell')))::INTEGER AS cell_x,
  (floor(((Ymin(stbox(p)) + Ymax(stbox(p))) / 2) / getvariable('cell')))::INTEGER AS cell_y
FROM pieces WHERE p IS NOT NULL;

CREATE OR REPLACE TABLE TripPieces AS
SELECT 'L2' AS layout, mmsi, cell_x || '/' || cell_y AS file, p FROM TripL2
UNION ALL
SELECT 'L3', mmsi, cell_x || '/' || cell_y, p FROM TripL3;

-- A trip the frame does not meet would draw an empty figure with a caption describing its pieces
SELECT CASE WHEN count(*) = 0
  THEN error('the vessel has no in-motion segment of the day inside the frame')
  ELSE 'trip pieces: ' || count(*) END AS trip_pieces FROM TripPieces;

-- The files the trip meets in the order of their cells, which make_jobs.py colours alike in both
-- layouts
COPY (SELECT file FROM (SELECT cell_x || '/' || cell_y AS file, cell_x, cell_y FROM TripL2
                        UNION SELECT cell_x || '/' || cell_y, cell_x, cell_y FROM TripL3)
      ORDER BY cell_x, cell_y)
TO (getvariable('stage') || '/trip_files.csv') (HEADER);

-- What the figure's caption states, per layout and file
SELECT layout, file, count(*) AS pieces, sum(numInstants(p)) AS instants
FROM TripPieces GROUP BY layout, file ORDER BY layout, file;

COPY (SELECT layout, file, trajectory(p) AS geom FROM TripPieces)
TO (getvariable('stage') || '/trip_tracks.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
COPY (SELECT layout, file, box(Xmin(stbox(p)), Ymin(stbox(p)), Xmax(stbox(p)), Ymax(stbox(p)))
        AS geom FROM TripPieces)
TO (getvariable('stage') || '/trip_boxes.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
