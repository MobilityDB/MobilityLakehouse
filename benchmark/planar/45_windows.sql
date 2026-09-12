-- The benchmark's query windows: four regions of the paper's query-area table, each a rectangle
-- in EPSG:25832, the metric frame of the corpus, crossed with four time windows. Three files:
--   windows_25832.csv  the rectangles themselves, what the layouts' boxes and trajectories are
--                      tested against;
--   windows_wgs84.csv  each rectangle transformed to WGS84 corner by corner, the polygon the H3
--                      experiments test their trips against: H3 reads geographic latitude and
--                      longitude only, and those trips are the corpus trips transformed the same
--                      way, instant by instant;
--   windows.psv        what the cell-cover harness reads, name|t0|t1|wkt, the period in Unix
--                      seconds and the WGS84 polygon.
--
-- The three files are written into the working directory; run it from the directory holding it:
--
--   duckdb -c ".read 45_windows.sql"

LOAD spatial;

CREATE OR REPLACE TEMP TABLE region(name, xmin, ymin, xmax, ymax) AS VALUES
  ('rodby_port', 651135.0, 6058230.0, 651422.0, 6058548.0),
  ('puttgarden', 644339.0, 6042108.0, 644896.0, 6042487.0),
  ('goteborg',   666538.0, 6392057.0, 679171.0, 6403745.0),
  ('belt',       640730.0, 6042487.0, 654100.0, 6058230.0);

CREATE OR REPLACE TEMP TABLE win(wname, t0, t1) AS VALUES
  ('1h',     TIMESTAMP '2026-01-15 08:00:00', TIMESTAMP '2026-01-15 09:00:00'),
  ('1day',   TIMESTAMP '2026-01-15 08:00:00', TIMESTAMP '2026-01-16 08:00:00'),
  ('1week',  TIMESTAMP '2026-01-15 08:00:00', TIMESTAMP '2026-01-22 08:00:00'),
  ('1month', TIMESTAMP '2026-01-01 00:00:00', TIMESTAMP '2026-02-01 00:00:00');

COPY (
  SELECT r.name || '_' || w.wname AS name, r.xmin, r.ymin, r.xmax, r.ymax, w.t0, w.t1
  FROM region r, win w ORDER BY r.name, w.wname
) TO 'windows_25832.csv' (FORMAT csv, HEADER false);

CREATE OR REPLACE TEMP TABLE outline AS
SELECT name,
  ST_AsText(ST_Transform(ST_MakeEnvelope(xmin, ymin, xmax, ymax), 'EPSG:25832', 'EPSG:4326',
                         always_xy := true)) AS wkt
FROM region;

COPY (
  SELECT g.name || '_' || w.wname AS name, g.wkt, w.t0, w.t1
  FROM outline g, win w ORDER BY g.name, w.wname
) TO 'windows_wgs84.csv' (FORMAT csv, HEADER false);

COPY (
  SELECT g.name || '_' || w.wname AS name, epoch(w.t0)::BIGINT AS t0,
    epoch(w.t1)::BIGINT AS t1, g.wkt
  FROM outline g, win w ORDER BY g.name, w.wname
) TO 'windows.psv'
  (FORMAT csv, DELIMITER '|', HEADER false, QUOTE '');
