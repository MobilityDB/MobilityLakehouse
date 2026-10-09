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

-- The region cells the spatial layouts partition by, asked of the tiler itself: `spaceTiles` is
-- what answers the tiles of an area, and it carries the origin and the border rule that decide
-- where a boundary falls. Deriving the boundaries as multiples of the cell instead would restate
-- the tiler's arithmetic and hold only while 41_part_layouts.sh leaves `sorigin` at its default,
-- with nothing to report the day it does not.
--
-- The cell is CELL_SIZE, the size that script splits on, and the frame is the area the figures
-- cover, so the layer holds the cells a figure can show and no others.
CREATE OR REPLACE TABLE Tiles AS
SELECT Xmin(tile) AS x0, Ymin(tile) AS y0, Xmax(tile) AS x1, Ymax(tile) AS y1
FROM spaceTiles(
  stbox('SRID=25832;STBOX X((' || getvariable('fx0') || ',' || getvariable('fy0') || '),(' ||
        getvariable('fx1') || ',' || getvariable('fy1') || '))'),
  getvariable('cell'), getvariable('cell'));

-- A frame always meets at least one cell, so an empty layer means the tiler answered nothing and
-- the figure would be drawn without the grid it is about.
SELECT CASE WHEN count(*) = 0
  THEN error('spaceTiles answered no cell for the frame; the grid layer would be empty')
  ELSE 'tiles: ' || count(*) END AS tiles FROM Tiles;

COPY (SELECT box(x0, y0, x1, y1) AS geom FROM Tiles)
TO (getvariable('stage') || '/tiles.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:25832', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

-- The same cells' boundaries, each drawn once as one line across the cells: a figure that dashes
-- the boundaries over a map draws these, since two neighbouring cells share an edge, and the edge
-- drawn twice with two dash phases reads as solid in places
COPY (
  SELECT ST_GeomFromText('LINESTRING(' || x || ' ' || (SELECT min(y0) FROM Tiles) || ',' || x ||
                         ' ' || (SELECT max(y1) FROM Tiles) || ')') AS geom
  FROM (SELECT x0 AS x FROM Tiles UNION SELECT x1 FROM Tiles)
  UNION ALL
  SELECT ST_GeomFromText('LINESTRING(' || (SELECT min(x0) FROM Tiles) || ' ' || y || ',' ||
                         (SELECT max(x1) FROM Tiles) || ' ' || y || ')')
  FROM (SELECT y0 AS y FROM Tiles UNION SELECT y1 FROM Tiles)
) TO (getvariable('stage') || '/tile_lines.gpkg')
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

-- The positions of each segment in time order, kept as a list so the geometry each segment needs
-- is built from it: a line where there are two or more, the position itself where there is one.
CREATE OR REPLACE TABLE SegPos AS
SELECT s.seg, s.segment_type AS type, count(*) AS n,
  list(ST_Point(c.lon, c.lat) ORDER BY c.t) AS pts
FROM Seg s JOIN read_parquet(getvariable('out') || '/clean/*.parquet') c
  ON c.mmsi = getvariable('vessel') AND c.t BETWEEN s.trip_tmin AND s.trip_tmax
GROUP BY s.seg, s.segment_type;

-- A segment of two positions or more is a line; one of a single position is a point, and it is
-- drawn as one rather than dropped. A caption states the segments the segmentation found, so a
-- figure that draws only the lines shows fewer shapes than its own caption counts.
COPY (SELECT seg, type, ST_MakeLine(pts) AS geom FROM SegPos WHERE n >= 2)
TO (getvariable('stage') || '/track_segments.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

COPY (SELECT seg, type, pts[1] AS geom FROM SegPos WHERE n = 1)
TO (getvariable('stage') || '/track_points.gpkg')
WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326', LAYER_CREATION_OPTIONS 'GEOMETRY_NAME=geom');

-- Every segment carries at least one position and is drawn exactly once, as a line or as a point
SELECT CASE WHEN (SELECT count(*) FROM SegPos) <> (SELECT count(*) FROM Seg)
  THEN error('a segment of the day carries no cleaned position')
  ELSE 'drawn: ' || (SELECT count(*) FROM SegPos WHERE n >= 2) || ' lines, ' ||
       (SELECT count(*) FROM SegPos WHERE n = 1) || ' points' END AS segments_drawn;

-- The frame of each render, in the rendering CRS, each with a 4:3 aspect. The tiling frames are
-- the study's own area and the strait the queries name; the track frames are the vessel's extent
-- with a margin, so the figure follows the data rather than a coordinate written twice. The frames
-- of 02_trip_export.sql, 03_crossing_export.sql and 04_usefulness_export.sql are stated here too,
-- since those exports cut their layers to them.
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
    UNION ALL SELECT 'tiling_L2_trip', 638000.0, 6041000.0, 662000.0, 6059000.0
    UNION ALL SELECT 'tiling_L3_trip', 638000.0, 6041000.0, 662000.0, 6059000.0
    UNION ALL SELECT 'q101_overview', 642500.0, 6041250.0, 653300.0, 6059250.0
    UNION ALL SELECT 'q101_rodby', 649780.0, 6057390.0, 652780.0, 6059640.0
    UNION ALL SELECT 'q101_puttgarden', 643120.0, 6040800.0, 646120.0, 6043050.0
    UNION ALL SELECT 'usefulness_month', 210000.0, 5937000.0, 910000.0, 6462000.0
    UNION ALL SELECT 'usefulness_query', 631415.0, 6038358.0, 663415.0, 6062358.0
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
