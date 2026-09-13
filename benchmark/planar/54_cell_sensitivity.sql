-- The file-level sensitivity of the spatial layouts to their region cell, computed from the
-- stored bounds of L0 alone, without building a layout. Each segment is counted in every cell of
-- a square grid its box overlaps, weighted by the bytes of its trajectory: the files a layout
-- cutting at that grid stores it in. A query region then fetches the cells it overlaps, which is
-- what a file-level pruner reads for it; row-group skipping and the clipping of a piece to its cell
-- are left out, so a share here bounds from above what the layouts read. Two files:
--   cell_size.csv  per cell size, 12.5 to 200 km: the non-empty cells, which are the files of a
--                  compact layout; the cells per segment and the bytes stored per byte of L0; and
--                  per query region the cells it overlaps and their share of the stored bytes;
--   density.csv    at the layouts' 50 km cell, that share for the four query regions, which lie in
--                  the busiest corridors, and for four open-water boxes the size of the belt, with
--                  the segments whose box centre falls in each and the pruning factor, 100 over
--                  the share.
--
-- The caller sets `out`, the run holding L0, and `windows`, the query windows file
-- (windows_25832.csv); the two files are written into the working directory:
--
--   duckdb -cmd "SET VARIABLE out = '<run>'" \
--     -cmd "SET VARIABLE windows = '<planar>/windows_25832.csv'" -c ".read 54_cell_sensitivity.sql"

CREATE OR REPLACE TEMP TABLE bounds AS
SELECT octet_length(trip) AS w, trip_xmin AS xmin, trip_ymin AS ymin, trip_xmax AS xmax,
  trip_ymax AS ymax
FROM read_parquet(getvariable('out') || '/L0/*/*/*.parquet', hive_partitioning = false);

CREATE OR REPLACE TEMP TABLE size(s) AS VALUES
  (12500.0), (25000.0), (50000.0), (100000.0), (200000.0);

CREATE OR REPLACE TEMP TABLE incid AS
SELECT z.s, b.w, gx.cx, gy.cy
FROM bounds b, size z,
  LATERAL unnest(generate_series(floor(b.xmin / z.s)::BIGINT, floor(b.xmax / z.s)::BIGINT))
    AS gx(cx),
  LATERAL unnest(generate_series(floor(b.ymin / z.s)::BIGINT, floor(b.ymax / z.s)::BIGINT))
    AS gy(cy);

-- The query regions, one row each out of the windows file, and four open-water boxes of the belt's
-- size (13.37 by 15.74 km) in the North Sea, the Skagerrak and the Kattegat.
CREATE OR REPLACE TEMP TABLE region AS
SELECT DISTINCT regexp_replace(name, '_[^_]*$', '') AS region, 'corridor' AS kind,
  xmin, ymin, xmax, ymax
FROM read_csv(getvariable('windows'), header = false, columns = {
  'name': 'VARCHAR', 'xmin': 'DOUBLE', 'ymin': 'DOUBLE', 'xmax': 'DOUBLE', 'ymax': 'DOUBLE',
  't0': 'TIMESTAMP', 't1': 'TIMESTAMP'});
INSERT INTO region VALUES
  ('north_sea_west',  'open water', 242631.9, 6183191.2, 256001.9, 6198934.2),
  ('north_sea_south', 'open water', 266695.5, 6036920.7, 280065.5, 6052663.7),
  ('skagerrak',       'open water', 510998.3, 6432011.4, 524368.3, 6447754.4),
  ('kattegat',        'open water', 628377.8, 6267154.9, 641747.8, 6282897.9);

CREATE OR REPLACE TEMP TABLE total AS
SELECT s, count(DISTINCT (cx, cy)) AS cells, sum(w) AS wsum,
  count(*) / (SELECT count(*) FROM bounds) AS cells_per_segment,
  sum(w) / (SELECT sum(w) FROM bounds) AS byte_replication
FROM incid GROUP BY s;

CREATE OR REPLACE TEMP TABLE touched AS
SELECT z.s, r.region, count(DISTINCT (i.cx, i.cy)) AS region_cells,
  coalesce(sum(i.w), 0) AS rw
FROM size z CROSS JOIN region r
LEFT JOIN incid i ON i.s = z.s
  AND i.cx BETWEEN floor(r.xmin / z.s)::BIGINT AND floor(r.xmax / z.s)::BIGINT
  AND i.cy BETWEEN floor(r.ymin / z.s)::BIGINT AND floor(r.ymax / z.s)::BIGINT
GROUP BY z.s, r.region;

COPY (
  SELECT t.s / 1000 AS cell_km, t.cells, round(t.cells_per_segment, 3) AS cells_per_segment,
    round(t.byte_replication, 3) AS byte_replication, u.region, u.region_cells,
    round(100 * u.rw / t.wsum, 3) AS share_pct
  FROM total t JOIN touched u USING (s) JOIN region r USING (region)
  WHERE r.kind = 'corridor'
  ORDER BY t.s, u.region
) TO 'cell_size.csv' (FORMAT csv, HEADER true);

COPY (
  SELECT r.region, r.kind, round((r.xmax - r.xmin) * (r.ymax - r.ymin) / 1e6, 3) AS area_km2,
    (SELECT count(*) FROM bounds b
     WHERE (b.xmin + b.xmax) / 2 BETWEEN r.xmin AND r.xmax
       AND (b.ymin + b.ymax) / 2 BETWEEN r.ymin AND r.ymax) AS segments,
    round(100 * u.rw / t.wsum, 3) AS share_pct,
    round(t.wsum / nullif(u.rw, 0), 1) AS pruning
  FROM region r JOIN touched u USING (region) JOIN total t USING (s)
  WHERE t.s = 50000.0
  ORDER BY r.kind, r.region
) TO 'density.csv' (FORMAT csv, HEADER true);
