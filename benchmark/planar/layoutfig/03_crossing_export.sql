-- Export the layers of the query-trip figures, q101_overview, q101_rodby and q101_puttgarden: one
-- ferry crossing between the two ports of the query of both ports, as L0 stores it (one row, one
-- box) and as L3 stores it (its pieces), each L3 piece classed by what the query does with it over
-- the query's window:
--   file  in a file the catalog discards, its cell being the cell of neither port;
--   read  in a file the catalog keeps, its box overlapping neither port, so the box test rejects it;
--   mbb   its box overlapping a port, so the box test keeps it;
-- and the part of each kept piece inside its port, which refinement (atStbox) keeps.
--
-- The ports and the window are the paper's, rodby_port_1day and puttgarden_1day of
-- windows_25832.csv; a port's file is the cell of the region grid holding its center, the cell
-- 41_part_layouts.sh files the pieces around it in. A piece whose box is shorter than `small` on
-- both sides is invisible at the scale of the overview, and is ringed in its own colour where a
-- frame shows it, so every piece the caption counts can be found on the figure.
--
-- Variables: out (the run), stage (where the GeoPackages go and frames.csv is), windows
-- (windows_25832.csv), cell (CELL_SIZE), crossing_vessel (the MMSI drawn), crossing_t0 and
-- crossing_t1 (the time span the crossing's in-motion segment lies in), small (in meters).
INSTALL spatial;
LOAD spatial;
SET geometry_always_xy = true;

CREATE OR REPLACE MACRO box(x0, y0, x1, y1) AS
  ST_GeomFromText('POLYGON((' || x0 || ' ' || y0 || ',' || x1 || ' ' || y0 || ',' ||
                  x1 || ' ' || y1 || ',' || x0 || ' ' || y1 || ',' || x0 || ' ' || y0 || '))');

CREATE OR REPLACE TABLE Port AS
SELECT column0 AS p, column1 AS x0, column2 AS y0, column3 AS x1, column4 AS y1,
  column5 AS t0, column6 AS t1,
  (floor((column1 + column3) / 2 / getvariable('cell')))::INTEGER || '/' ||
  (floor((column2 + column4) / 2 / getvariable('cell')))::INTEGER AS cell
FROM read_csv(getvariable('windows'), header = false)
WHERE column0 IN ('rodby_port_1day', 'puttgarden_1day');
SELECT CASE WHEN count(*) <> 2 THEN error('windows_25832.csv states the two ports of the query once each')
  ELSE 'ports: ' || string_agg(p || ' in cell ' || cell, ', ') END AS ports FROM Port;

CREATE OR REPLACE MACRO in_span(t) AS
  t >= cast(getvariable('crossing_t0') AS TIMESTAMP) AND t <= cast(getvariable('crossing_t1') AS TIMESTAMP);

-- The crossing as L0 stores it: one row, one box
CREATE OR REPLACE TABLE CrossingL0 AS
SELECT * FROM read_parquet(getvariable('out') || '/L0/*/*/*.parquet', hive_partitioning = false)
WHERE mmsi = getvariable('crossing_vessel') AND segment_type = 'In motion'
  AND in_span(trip_tmin) AND in_span(trip_tmax);
SELECT CASE WHEN count(*) <> 1 THEN error('the crossing is not one in-motion segment of L0')
  ELSE 'L0: one row, ' || min(trip_tmin) || ' to ' || max(trip_tmax) END AS crossing
FROM CrossingL0;

-- The crossing as L3 stores it: its pieces, with the cell of their file
CREATE OR REPLACE TABLE CrossingL3 AS
SELECT *, regexp_extract(filename, 'cell_x=(-?[0-9]+)', 1) || '/' ||
  regexp_extract(filename, 'cell_y=(-?[0-9]+)', 1) AS cell
FROM read_parquet(getvariable('out') || '/layouts_daily/L3/day-*/**/*.parquet', filename = true,
                  hive_partitioning = false)
WHERE mmsi = getvariable('crossing_vessel') AND segment_type = 'In motion'
  AND in_span(trip_tmin) AND in_span(trip_tmax);

CREATE OR REPLACE TABLE Fate AS
SELECT c.*,
  CASE WHEN cell NOT IN (SELECT cell FROM Port) THEN 'file'
       WHEN EXISTS (SELECT 1 FROM Port q WHERE q.cell = c.cell AND c.trip_xmin <= q.x1 AND
                    c.trip_xmax >= q.x0 AND c.trip_ymin <= q.y1 AND c.trip_ymax >= q.y0)
         THEN 'mbb'
       ELSE 'read' END AS fate
FROM CrossingL3 c;

-- What the figure's caption states: the pieces by fate and file, with their positions
SELECT fate, cell, count(*) AS pieces, sum(numInstants(tgeompointFromEWKB(trip))) AS instants
FROM Fate GROUP BY ALL ORDER BY fate, cell;

-- The part of each kept piece that refinement keeps, the piece restricted to its port and window
CREATE OR REPLACE TABLE Kept AS
SELECT q.p, trajectory(atStbox(tgeompointFromEWKB(f.trip), stbox('SRID=25832;STBOX XT(((' ||
  q.x0 || ',' || q.y0 || '),(' || q.x1 || ',' || q.y1 || ')),[' || q.t0 || '+00,' || q.t1 ||
  '+00])'))) AS geom
FROM Fate f JOIN Port q ON q.cell = f.cell WHERE f.fate = 'mbb';
SELECT p, count(*) FILTER (WHERE geom IS NOT NULL) AS refined_pieces FROM Kept GROUP BY p;

-- The pieces too small to see, ringed by a circle sized to the frame that shows them
CREATE OR REPLACE TABLE Small AS
SELECT fate, (trip_xmin + trip_xmax) / 2 AS cx, (trip_ymin + trip_ymax) / 2 AS cy,
  numInstants(tgeompointFromEWKB(trip)) - 1 AS segments,
  trip_tmax - trip_tmin AS duration,
  round(trip_xmax - trip_xmin) AS width, round(trip_ymax - trip_ymin) AS height
FROM Fate
WHERE trip_xmax - trip_xmin < getvariable('small') AND trip_ymax - trip_ymin < getvariable('small');
SELECT * FROM Small;

COPY (SELECT trajectory(tgeompointFromEWKB(trip)) AS geom FROM CrossingL0)
TO (getvariable('stage') || '/q101_track.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
COPY (SELECT box(trip_xmin, trip_ymin, trip_xmax, trip_ymax) AS geom FROM CrossingL0)
TO (getvariable('stage') || '/q101_l0box.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
COPY (SELECT fate, cell, box(trip_xmin, trip_ymin, trip_xmax, trip_ymax) AS geom FROM Fate)
TO (getvariable('stage') || '/q101_pieces.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
COPY (SELECT p, geom FROM Kept WHERE geom IS NOT NULL)
TO (getvariable('stage') || '/q101_kept.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
COPY (SELECT p, box(x0, y0, x1, y1) AS geom FROM Port)
TO (getvariable('stage') || '/q101_ports.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
-- The ring's radius, in meters, is sized to each frame that shows the piece
COPY (SELECT s.fate, f.fig, ST_Buffer(ST_Point(s.cx, s.cy), r.radius, 48) AS geom
      FROM Small s,
        read_csv(getvariable('stage') || '/frames.csv', header = true) f,
        (VALUES ('q101_overview', 260.0), ('q101_puttgarden', 75.0)) r(fig, radius)
      WHERE f.fig = r.fig AND s.cx BETWEEN f.x0 AND f.x1 AND s.cy BETWEEN f.y0 AND f.y1)
TO (getvariable('stage') || '/q101_small.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
