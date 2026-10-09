-- Export the layers and the table of the usefulness figure, from a month of AIS data to the few
-- files one query reads:
--   usefulness_month     the month's in-motion segments of L0, with the query region;
--   usefulness_query     the query region over its window: the month's traffic, the pieces stored in
--                        the L3 files the catalog admits, those whose box intersects the query, and
--                        the region cells;
--   usefulness_files     every L3 file as a (day, cell) cell of a grid, drawn by usefulness_grid.py;
--   usefulness_admitted  the same grid, the files the catalog admits marked.
-- A file is admitted when its column minima and maxima, the bounds an Iceberg manifest records,
-- intersect the query's box and window: the same comparison the catalog makes, applied to the
-- files' own columns rather than read back from a manifest.
--
-- The month's tracks are simplified to 50 m for drawing alone: the figure is 700 km wide at 1100
-- pixels, so a pixel spans more than ten times that, and no count below reads a simplified track.
--
-- Variables: out (the run), stage (where the GeoPackages and use_files.csv go, frames.csv is),
-- windows (windows_25832.csv), use_window (the window of the query drawn).
INSTALL spatial;
LOAD spatial;
SET geometry_always_xy = true;

CREATE OR REPLACE MACRO box(x0, y0, x1, y1) AS
  ST_GeomFromText('POLYGON((' || x0 || ' ' || y0 || ',' || x1 || ' ' || y0 || ',' ||
                  x1 || ' ' || y1 || ',' || x0 || ' ' || y1 || ',' || x0 || ' ' || y0 || '))');

CREATE OR REPLACE TABLE UseQuery AS
SELECT DISTINCT column1 AS x0, column2 AS y0, column3 AS x1, column4 AS y1,
  column5 AS t0, column6 AS t1
FROM read_csv(getvariable('windows'), header = false)
WHERE column0 = getvariable('use_window');
SELECT CASE WHEN count(*) <> 1 THEN error('windows_25832.csv states the query window once')
  ELSE 'query: ' || getvariable('use_window') END AS query FROM UseQuery;

-- (a) the month in L0
CREATE OR REPLACE TABLE UseL0 AS
SELECT * FROM read_parquet(getvariable('out') || '/L0/*/*/*.parquet', hive_partitioning = false);
SELECT count(*) AS segments, count(DISTINCT mmsi) AS vessels,
  count(*) FILTER (WHERE segment_type = 'In motion') AS moving FROM UseL0;
COPY (SELECT ST_Simplify(trajectory(tgeompointFromEWKB(trip)), 50) AS geom
      FROM UseL0 WHERE segment_type = 'In motion')
TO (getvariable('stage') || '/use_month_tracks.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

-- (c) and (d) the files of L3 and the catalog's verdict on each
CREATE OR REPLACE TABLE UseL3 AS
SELECT *, regexp_extract(filename, 'day-([0-9-]+)', 1) AS fday,
  regexp_extract(filename, 'cell_x=(-?[0-9]+)', 1)::INTEGER AS cx,
  regexp_extract(filename, 'cell_y=(-?[0-9]+)', 1)::INTEGER AS cy
FROM read_parquet(getvariable('out') || '/layouts_daily/L3/day-*/**/*.parquet', filename = true,
                  hive_partitioning = false);
CREATE OR REPLACE TABLE UseFiles AS
SELECT filename, fday, cx, cy, count(*) AS nrows,
  min(trip_xmin) AS mnx, max(trip_xmax) AS mxx, min(trip_ymin) AS mny, max(trip_ymax) AS mxy,
  min(trip_tmin) AS mnt, max(trip_tmax) AS mxt
FROM UseL3 GROUP BY ALL;
CREATE OR REPLACE TABLE UseVerdict AS
SELECT f.*, (f.mnx <= q.x1 AND f.mxx >= q.x0 AND f.mny <= q.y1 AND f.mxy >= q.y0 AND
             f.mnt <= q.t1 AND f.mxt >= q.t0) AS admitted
FROM UseFiles f, UseQuery q;

-- What the figure's caption states: the files and pieces admitted, and their share of the bytes
SELECT count(*) AS files, count(DISTINCT (cx, cy)) AS cells,
  count(*) FILTER (WHERE admitted) AS admitted, sum(nrows) AS pieces,
  sum(nrows) FILTER (WHERE admitted) AS pieces_admitted FROM UseVerdict;
-- read_blob answers a file's size without reading its content when the content is not asked for
SELECT sum(size) AS bytes,
  sum(size) FILTER (WHERE filename IN (SELECT filename FROM UseVerdict WHERE admitted))
    AS bytes_admitted,
  round(100.0 * bytes_admitted / bytes, 2) AS percent_admitted
FROM read_blob(getvariable('out') || '/layouts_daily/L3/day-*/**/*.parquet');
SELECT fday, cx, cy, nrows FROM UseVerdict WHERE admitted ORDER BY fday, cx, cy;
COPY (SELECT fday, cx, cy, nrows, admitted FROM UseVerdict ORDER BY fday, cx, cy)
TO (getvariable('stage') || '/use_files.csv') (HEADER);

-- (b) the pieces the query reads, and those whose box intersects it
CREATE OR REPLACE TABLE UseRead AS
SELECT l.*, (l.trip_xmin <= q.x1 AND l.trip_xmax >= q.x0 AND l.trip_ymin <= q.y1 AND
             l.trip_ymax >= q.y0 AND l.trip_tmin <= q.t1 AND l.trip_tmax >= q.t0) AS mbb
FROM UseL3 l JOIN UseVerdict v USING (filename), UseQuery q WHERE v.admitted;
SELECT count(*) AS pieces_read, count(*) FILTER (WHERE mbb) AS pieces_mbb,
  count(DISTINCT mmsi) FILTER (WHERE mbb) AS vessels_mbb FROM UseRead;
COPY (SELECT mbb, trajectory(tgeompointFromEWKB(trip)) AS geom FROM UseRead)
TO (getvariable('stage') || '/use_pieces.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
COPY (SELECT box(x0, y0, x1, y1) AS geom FROM UseQuery)
TO (getvariable('stage') || '/use_region.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');
