-- Export the layers of the tiling and segmentation figures as GeoPackages, one per layer, each
-- stating its SRID and geometry type, since QGIS draws nothing from a layer without them.
--
-- Everything here is a plain column of the run, after `#getvariable` in 54_cell_sensitivity.sql:
-- a segment's stored bounds are trip_xmin..trip_ymax and its tile is cell_x/cell_y, so no
-- trajectory is decoded and the export needs no MEOS. The cleaned positions carry the geometry the
-- segmentation figure draws, a segment's trajectory being exactly the cleaned positions of its
-- vessel inside its own time span.
--
-- Variables: out (the run), raw (the raw zone), stage (where the GeoPackages go), windows
-- (windows_25832.csv), day, vessel, and the frame the boxes are cut to, fx0 fy0 fx1 fy1, in
-- EPSG:25832.
INSTALL spatial;
LOAD spatial;
SET geometry_always_xy = true;

CREATE OR REPLACE MACRO box(x0, y0, x1, y1) AS
  ST_GeomFromText('POLYGON((' || x0 || ' ' || y0 || ',' || x1 || ' ' || y0 || ',' ||
                  x1 || ' ' || y1 || ',' || x0 || ' ' || y1 || ',' || x0 || ' ' || y0 || '))');

-- The stored bounds of every segment of the day that meets the frame, by layout. A box is drawn as
-- an outline, so the figure shows how far each tiling cuts a trajectory down. A day of a
-- partitioned layout is a directory of cell_x=/cell_y= parts, so the glob reaches through them.
CREATE OR REPLACE TABLE Boxes AS
SELECT 'L2' AS layout, trip_xmin AS x0, trip_ymin AS y0, trip_xmax AS x1, trip_ymax AS y1
FROM read_parquet(getvariable('out') || '/layouts_daily/L2/day-' || getvariable('day') ||
                  '/**/*.parquet', hive_partitioning = false)
UNION ALL
SELECT 'L3', trip_xmin, trip_ymin, trip_xmax, trip_ymax
FROM read_parquet(getvariable('out') || '/layouts_daily/L3/day-' || getvariable('day') ||
                  '/**/*.parquet', hive_partitioning = false);

COPY (
  SELECT layout, box(x0, y0, x1, y1) AS geom FROM Boxes
  WHERE x1 >= getvariable('fx0') AND x0 <= getvariable('fx1')
    AND y1 >= getvariable('fy0') AND y0 <= getvariable('fy1')
) TO (getvariable('stage') || '/boxes.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

-- The grid the spatial layouts cut on: cell_x and cell_y index a 50 km cell on a zero origin, so
-- the boundaries are the multiples of the cell inside the frame. They are drawn rather than
-- derived from the boxes, because a boundary no segment reaches is still a boundary.
COPY (
  WITH X AS (SELECT v * 50000.0 AS c FROM range(
        cast(floor(getvariable('fx0') / 50000.0) AS BIGINT),
        cast(ceil(getvariable('fx1') / 50000.0) AS BIGINT) + 1) t(v)),
  Y AS (SELECT v * 50000.0 AS c FROM range(
        cast(floor(getvariable('fy0') / 50000.0) AS BIGINT),
        cast(ceil(getvariable('fy1') / 50000.0) AS BIGINT) + 1) t(v))
  SELECT ST_GeomFromText('LINESTRING(' || c || ' ' || getvariable('fy0') || ',' ||
                         c || ' ' || getvariable('fy1') || ')') AS geom FROM X
  UNION ALL
  SELECT ST_GeomFromText('LINESTRING(' || getvariable('fx0') || ' ' || c || ',' ||
                         getvariable('fx1') || ' ' || c || ')') FROM Y
) TO (getvariable('stage') || '/tiles.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

-- The query regions the tiles are compared against: a tile prunes only while it is smaller than
-- the region asked about.
COPY (
  SELECT name, box(x0, y0, x1, y1) AS geom
  FROM (SELECT DISTINCT column0 AS name, column1 AS x0, column2 AS y0,
               column3 AS x1, column4 AS y1
        FROM read_csv(getvariable('windows'), header = false))
) TO (getvariable('stage') || '/region.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

-- One vessel's raw reports of the day, before cleaning: the left panel of the segmentation figure.
COPY (
  SELECT ST_Point(Longitude, Latitude) AS geom
  FROM read_parquet(getvariable('raw') || '/aisdk-' || getvariable('day') || '.parquet')
  WHERE MMSI = getvariable('vessel')
    AND Longitude BETWEEN -180 AND 180 AND Latitude BETWEEN -90 AND 90
) TO (getvariable('stage') || '/track_raw.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

-- The same vessel's segments after cleaning and segmentation: one line per segment through the
-- cleaned positions inside its own time span, carrying the type the segmentation assigned.
CREATE OR REPLACE TABLE Seg AS
SELECT row_number() OVER (ORDER BY trip_tmin) AS seg, segment_type, trip_tmin, trip_tmax
FROM read_parquet(getvariable('out') || '/L0/*/*/*.parquet', hive_partitioning = false)
WHERE mmsi = getvariable('vessel')
  AND trip_tmin < cast(getvariable('day') AS DATE) + INTERVAL 1 DAY
  AND trip_tmax >= cast(getvariable('day') AS DATE);

COPY (
  SELECT s.seg, s.segment_type AS type,
    ST_MakeLine(list(ST_Point(c.lon, c.lat) ORDER BY c.t)) AS geom
  FROM Seg s JOIN read_parquet(getvariable('out') || '/clean/*.parquet') c
    ON c.mmsi = getvariable('vessel') AND c.t BETWEEN s.trip_tmin AND s.trip_tmax
  GROUP BY s.seg, s.segment_type
  HAVING count(*) >= 2
) TO (getvariable('stage') || '/track_segments.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

-- The frame of each render, in the rendering CRS, each with a 4:3 aspect. The tiling frames are
-- the study's own area and the strait the queries name; the track frames are the vessel's extent
-- with a margin, so the figure follows the data rather than a coordinate written twice.
COPY (
  WITH L AS (
    SELECT min(ST_X(geom)) lx0, min(ST_Y(geom)) ly0,
           max(ST_X(geom)) lx1, max(ST_Y(geom)) ly1
    FROM ST_Read(getvariable('stage') || '/track_raw.gpkg')),
  T AS (
    SELECT ST_X(ST_Transform(ST_Point(lx0, ly0), 'EPSG:4326', 'EPSG:25832')) mx0,
           ST_Y(ST_Transform(ST_Point(lx0, ly0), 'EPSG:4326', 'EPSG:25832')) my0,
           ST_X(ST_Transform(ST_Point(lx1, ly1), 'EPSG:4326', 'EPSG:25832')) mx1,
           ST_Y(ST_Transform(ST_Point(lx1, ly1), 'EPSG:4326', 'EPSG:25832')) my1
    FROM L),
  B AS (
    SELECT (mx0 + mx1) / 2 AS cx, (my0 + my1) / 2 AS cy,
      greatest(mx1 - mx0, (my1 - my0) * 4 / 3) * 0.62 AS hw FROM T),
  F(fig, x0, y0, x1, y1) AS (
    SELECT 'L2_tiling_wide', 600000.0, 6013000.0, 700000.0, 6088000.0
    UNION ALL SELECT 'L3_tiling_wide', 600000.0, 6013000.0, 700000.0, 6088000.0
    UNION ALL SELECT 'L2_tiling_zoom', 628000.0, 6033000.0, 672000.0, 6066000.0
    UNION ALL SELECT 'L3_tiling_zoom', 628000.0, 6033000.0, 672000.0, 6066000.0
    UNION ALL SELECT 'segment_raw', cx - hw, cy - hw * 3 / 4, cx + hw, cy + hw * 3 / 4 FROM B
    UNION ALL SELECT 'clean_segmented', cx - hw, cy - hw * 3 / 4, cx + hw, cy + hw * 3 / 4 FROM B)
  SELECT fig, round(x0) AS x0, round(y0) AS y0, round(x1) AS x1, round(y1) AS y1 FROM F
) TO (getvariable('stage') || '/frames.csv') WITH (FORMAT CSV, HEADER);

-- What the segmentation figure's caption states, read off the run rather than carried
SELECT (SELECT count(*) FROM read_parquet(getvariable('raw') || '/aisdk-' ||
          getvariable('day') || '.parquet') WHERE MMSI = getvariable('vessel')) AS raw_reports,
  (SELECT count(*) FROM Seg) AS segments,
  (SELECT count(*) FROM Seg WHERE segment_type = 'In motion') AS in_motion,
  (SELECT count(*) FROM Seg WHERE segment_type = 'Stationary') AS stationary;
