-- Setup for queries/iceberg_region/: the region form of the ten benchmark queries.
--
-- WHY THIS FOLDER EXISTS
-- queries/iceberg/*.sql pin the query region to the Rodby-Puttgarden alert belt, a
-- RECTANGLE, and test it with atStbox(traj, stbox(envelope, span)). atStbox clips to a
-- space-time BOX, so for the belt it is exact: the box IS the region. Point the same
-- query at a region that is not a rectangle (a protected area, a raster-derived
-- footprint) and stage 2 still tests the box, so the query answers a different
-- question. Measured on `Smålandsfarvandet` (a Natura 2000 marine site), the box form
-- reports 63 vessels present where the polygon form reports 23, 2 589 km travelled
-- against 396, and 12 collision pairs within 300 m against 0.
--
-- These files keep the two stages separate and give each the right geometry:
--   stage 1 (prune)  the region's BOUNDING BOX vs the scalar sidecar columns. A box is
--                    all a catalog can push down, and it is only a necessary condition.
--   stage 2 (exact)  the region POLYGON, via atGeometry (eIntersects for Q4.2).
-- Joins, projections, grouping and the temporal clip are unchanged from
-- queries/iceberg/, so the two folders are comparable query for query.

INSTALL iceberg;  LOAD iceberg;
INSTALL httpfs;   LOAD httpfs;
LOAD '<path-to>/mobilityduck.duckdb_extension';
INSTALL spatial;  LOAD spatial;

CREATE SECRET (
    TYPE S3, KEY_ID 'admin', SECRET 'password',
    ENDPOINT 'localhost:9000', URL_STYLE 'path', USE_SSL false, REGION 'us-east-1'
);

ATTACH '' AS lake (
    TYPE ICEBERG, ENDPOINT 'http://localhost:8181', AUTHORIZATION_TYPE 'none'
);

-- The layout under test, exactly as in queries/iceberg/setup.sql.
CREATE OR REPLACE VIEW trips AS SELECT * FROM lake.ais.L3s;

-- BIND THE REGION
-- stage 2 reads the polygon from `region_geom` (and `region_geom_b` for Q4.1, the
-- two-region query). Any GEOMETRY in the trajectories' CRS (EPSG:32632) works.
-- Nine names in the registry are not unique, so ORDER BY a stable key rather than
-- relying on LIMIT 1 to pick the row you meant.
CREATE OR REPLACE TABLE region_geom AS
SELECT ST_GeomFromText(geom) AS g
FROM read_parquet('data/natural_areas/natural_areas.parquet')
WHERE name_eng = 'Smålandsfarvandet'
ORDER BY desig_eng, xmin, ymin LIMIT 1;

CREATE OR REPLACE TABLE region_geom_b AS
SELECT ST_GeomFromText(geom) AS g
FROM read_parquet('data/natural_areas/natural_areas.parquet')
WHERE name_eng = 'Nordvestlige Kattegat'
ORDER BY desig_eng, xmin, ymin LIMIT 1;

-- stage 1 reads the same region's bounding box from these variables. Keep them
-- consistent with region_geom: the prune must never exclude what the polygon admits.
SET VARIABLE rx0 = (SELECT ST_XMin(g) FROM region_geom);
SET VARIABLE rx1 = (SELECT ST_XMax(g) FROM region_geom);
SET VARIABLE ry0 = (SELECT ST_YMin(g) FROM region_geom);
SET VARIABLE ry1 = (SELECT ST_YMax(g) FROM region_geom);

SET VARIABLE sx0 = (SELECT ST_XMin(g) FROM region_geom_b);
SET VARIABLE sx1 = (SELECT ST_XMax(g) FROM region_geom_b);
SET VARIABLE sy0 = (SELECT ST_YMin(g) FROM region_geom_b);
SET VARIABLE sy1 = (SELECT ST_YMax(g) FROM region_geom_b);

-- Time window and date partitions, as in queries/iceberg/setup.sql.
SET VARIABLE t0   = TIMESTAMPTZ '2026-01-15 00:00:00+00';
SET VARIABLE t1   = TIMESTAMPTZ '2026-01-16 00:00:00+00';
SET VARIABLE tmid = TIMESTAMPTZ '2026-01-15 12:00:00+00';
SET VARIABLE d0   = DATE '2026-01-15';
SET VARIABLE d1   = DATE '2026-01-15';
