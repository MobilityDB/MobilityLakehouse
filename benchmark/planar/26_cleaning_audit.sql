-- What the cleaning removes, stage by stage, over [lo, hi): the reports and the vessels each rule
-- keeps, counted cumulatively from the raw zone, and the vessels it removes by ship type and by
-- the number of reports they sent. It reads what stages 1 and 2 of run_clean.sh write and runs
-- after them.
--
-- The field checks are those of 10_stage.sql, applied one after the other in its order, so the
-- last of them counts what stage 1 stages and the table states that count from the buckets beside
-- it; a difference between the two would be a defect of this audit. The rules of stage 2 are
-- counted from its outputs: the buckets deduplicated to one report per vessel and instant as
-- 20_clean.sql deduplicates them, the vessels and reports the walk accepts (the clean points), the
-- segments and the stops.
--
-- A vessel is removed at the first rule after which none of its reports remains. Its ship type is
-- the one it reports most often, and its reports are those it sent in [lo, hi), in decades.
--
-- Variables: raw_glob, lo, hi (timestamps), mid_csv (the MID table), out (the run directory).
-- Tables: audit_stage(step, rule, reports, vessels), audit_removed(removed_at, ship_type,
-- reports_class, vessels, reports).

CREATE OR REPLACE TEMP TABLE r AS
SELECT MMSI AS mmsi, ShipType AS ship_type,
  TypeOfMobile = 'Class A' AS f1,
  MMSI BETWEEN 200000000 AND 799999999
    AND MMSI // 1000000 IN (SELECT mid FROM read_csv(getvariable('mid_csv'))) AS f2,
  Longitude BETWEEN 3 AND 16 AND Latitude BETWEEN 53 AND 59 AS f3,
  (Draught IS NULL OR Draught <= 28.5) AND (Width IS NULL OR Width <= 75)
    AND (Length IS NULL OR Length <= 488) AS f4
FROM read_parquet(getvariable('raw_glob'))
WHERE T >= getvariable('lo')::TIMESTAMP AND T < getvariable('hi')::TIMESTAMP;

CREATE OR REPLACE TEMP TABLE b AS
SELECT mmsi, t FROM read_parquet(getvariable('out') || '/buckets/*/*.parquet');
CREATE OR REPLACE TEMP TABLE dd AS SELECT DISTINCT mmsi, t FROM b;
CREATE OR REPLACE TEMP TABLE c AS
SELECT mmsi, t FROM read_parquet(getvariable('out') || '/clean/*.parquet');
CREATE OR REPLACE TEMP TABLE sg AS
SELECT mmsi, segment_type FROM read_parquet(getvariable('out') || '/segments/*.parquet');

CREATE OR REPLACE TEMP TABLE audit_stage AS
SELECT * FROM (
  SELECT 1 AS step, 'raw reports' AS rule, count(*) AS reports, count(DISTINCT mmsi) AS vessels FROM r
  UNION ALL SELECT 2, 'Class A', count(*), count(DISTINCT mmsi) FROM r WHERE f1
  UNION ALL SELECT 3, 'ship-station MMSI', count(*), count(DISTINCT mmsi) FROM r WHERE f1 AND f2
  UNION ALL SELECT 4, 'in the study area', count(*), count(DISTINCT mmsi) FROM r
    WHERE f1 AND f2 AND f3
  UNION ALL SELECT 5, 'plausible dimensions', count(*), count(DISTINCT mmsi) FROM r
    WHERE f1 AND f2 AND f3 AND f4
  UNION ALL SELECT 6, 'staged (the buckets)', count(*), count(DISTINCT mmsi) FROM b
  UNION ALL SELECT 7, 'one report per vessel and instant', count(*), count(DISTINCT mmsi) FROM dd
  UNION ALL SELECT 8, 'vessels the walk keeps', count(*), count(DISTINCT mmsi) FROM dd
    WHERE mmsi IN (SELECT mmsi FROM c)
  UNION ALL SELECT 9, 'reports the walk accepts', count(*), count(DISTINCT mmsi) FROM c
  UNION ALL SELECT 10, 'segments', count(*), count(DISTINCT mmsi) FROM sg
  UNION ALL SELECT 11, 'segments in motion', count(*), count(DISTINCT mmsi) FROM sg
    WHERE segment_type = 'In motion')
ORDER BY step;

CREATE OR REPLACE TEMP TABLE audit_removed AS
WITH v AS (
  SELECT mmsi, mode(ship_type) AS ship_type, count(*) AS reports,
    bool_or(f1) AS k1, bool_or(f1 AND f2) AS k2, bool_or(f1 AND f2 AND f3) AS k3,
    bool_or(f1 AND f2 AND f3 AND f4) AS k4
  FROM r GROUP BY mmsi),
w AS (
  SELECT v.*, v.mmsi IN (SELECT mmsi FROM c) AS k5 FROM v)
SELECT removed_at, coalesce(ship_type, 'Undefined') AS ship_type,
  CASE WHEN reports < 10 THEN '1-9' WHEN reports < 100 THEN '10-99'
       WHEN reports < 1000 THEN '100-999' WHEN reports < 10000 THEN '1000-9999'
       ELSE '10000+' END AS reports_class,
  count(*) AS vessels, sum(reports) AS reports
FROM (
  SELECT *, CASE WHEN NOT k1 THEN 'Class A' WHEN NOT k2 THEN 'ship-station MMSI'
                 WHEN NOT k3 THEN 'in the study area' WHEN NOT k4 THEN 'plausible dimensions'
                 WHEN NOT k5 THEN 'the walk' END AS removed_at
  FROM w)
WHERE removed_at IS NOT NULL
GROUP BY ALL
ORDER BY removed_at, ship_type, reports_class;
