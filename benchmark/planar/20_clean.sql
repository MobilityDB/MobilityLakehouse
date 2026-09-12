-- Stage 2 of the planar pipeline: one vessel bucket cleaned and segmented over its whole period,
-- in EPSG:25832, under these rules:
--   dedup    one report per vessel and instant, ties broken by (lat, lon, ship_type, sog, nav), a
--            total order on the columns kept, so a rebuild keeps the same report;
--   walk     a report is kept when the last accepted one could reach it at 100 kn; the seed is
--            the earliest of the first eleven reports a majority of them can reach, reports
--            before it are walked backwards from it, and a vessel whose share of reports
--            unreachable from the report before exceeds one half is dropped whole;
--   pieces   a new piece after a silence over 600 s;
--   speed    the window-centroid speed of the positions over [t, t + 600 s] (the backward frame
--            where the forward one spans under 300 s), the reported SOG where neither does;
--   stops    a vessel becomes stopped at <= 0.2 kn and moving again at >= 0.5 kn, keeping its
--            state in between; a stopped run lasting at least 300 s is a stop.
-- The tables `clean` and `stop_run` hold the result; run_clean.sh writes them out.
--
-- Variables: bucket_glob.

-- A report is reachable from another when the straight line between them needs at most 100 kn.
CREATE OR REPLACE TEMP MACRO reach(d, dt) AS d <= 100 * dt * 1852 / 3600;
CREATE OR REPLACE TEMP MACRO dist(x1, y1, x2, y2) AS sqrt((x2 - x1) ^ 2 + (y2 - y1) ^ 2);

CREATE OR REPLACE TEMP TABLE kept AS
SELECT *, epoch(t) AS ts, row_number() OVER (PARTITION BY mmsi ORDER BY t) AS rn
FROM (
  SELECT * FROM read_parquet(getvariable('bucket_glob'))
  QUALIFY row_number() OVER (PARTITION BY mmsi, t
                             ORDER BY lat, lon, ship_type, sog, nav) = 1);

CREATE OR REPLACE TEMP TABLE keep_vessel AS
SELECT mmsi FROM (
  SELECT mmsi,
    NOT coalesce(reach(dist(lag(x) OVER w, lag(y) OVER w, x, y), ts - lag(ts) OVER w), true)
      AS unreachable
  FROM kept WINDOW w AS (PARTITION BY mmsi ORDER BY rn))
GROUP BY mmsi HAVING avg(unreachable::INTEGER) <= 0.5;

CREATE OR REPLACE TEMP TABLE pts AS
SELECT k.* FROM kept k JOIN keep_vessel USING (mmsi);

CREATE OR REPLACE TEMP TABLE seed AS
WITH head AS (SELECT mmsi, rn, ts, x, y FROM pts WHERE rn <= 11),
agree AS (
  SELECT a.mmsi, a.rn,
    count(*) FILTER (b.rn <> a.rn AND b.ts <> a.ts
                     AND reach(dist(a.x, a.y, b.x, b.y), abs(b.ts - a.ts))) AS ok,
    count(*) - 1 AS others
  FROM head a JOIN head b USING (mmsi)
  GROUP BY a.mmsi, a.rn)
SELECT mmsi, coalesce(min(rn) FILTER (ok * 2 >= others), 1) AS seed_rn
FROM agree GROUP BY mmsi;

-- The track from the seed on, cut into runs at each report unreachable from the report before
-- it; inside a run every step is reachable, so the walk decides only where each run is rejoined.
CREATE OR REPLACE TEMP TABLE fwd AS
SELECT *, sum(CASE WHEN NOT coalesce(reach(d_prev, dt_prev), true) THEN 1 ELSE 0 END)
            OVER (PARTITION BY mmsi ORDER BY rn) AS run_id
FROM (
  SELECT p.*, p.ts - lag(p.ts) OVER w AS dt_prev,
    dist(lag(p.x) OVER w, lag(p.y) OVER w, p.x, p.y) AS d_prev
  FROM pts p JOIN seed s USING (mmsi)
  WHERE p.rn >= s.seed_rn
  WINDOW w AS (PARTITION BY p.mmsi ORDER BY p.rn));

CREATE OR REPLACE TEMP TABLE runs AS
SELECT mmsi, run_id,
  list(rn ORDER BY rn) AS rns, list(ts ORDER BY rn) AS tss,
  list(x ORDER BY rn) AS xs, list(y ORDER BY rn) AS ys,
  max(rn) AS last_rn, arg_max(ts, rn) AS last_ts,
  arg_max(x, rn) AS last_x, arg_max(y, rn) AS last_y
FROM fwd GROUP BY mmsi, run_id;

-- One step per run, carrying the anchor, the last accepted report. `cut` is the first report of
-- the run reachable from the anchor; a run with none is dropped whole and leaves the anchor.
CREATE OR REPLACE TEMP TABLE walk AS
WITH RECURSIVE w AS (
  SELECT mmsi, run_id, 0::BIGINT AS cut, last_ts AS a_ts, last_x AS a_x, last_y AS a_y
  FROM runs WHERE run_id = 0
  UNION ALL
  SELECT r.mmsi, r.run_id,
    CASE WHEN k = 0 THEN r.last_rn + 1 ELSE r.rns[k] END,
    CASE WHEN k = 0 THEN e.a_ts ELSE r.last_ts END,
    CASE WHEN k = 0 THEN e.a_x ELSE r.last_x END,
    CASE WHEN k = 0 THEN e.a_y ELSE r.last_y END
  FROM w e
  JOIN runs r ON r.mmsi = e.mmsi AND r.run_id = e.run_id + 1
  CROSS JOIN LATERAL (
    SELECT coalesce(list_position(list_transform(range(1, length(r.rns) + 1),
      lambda i: r.tss[i] > e.a_ts
           AND reach(dist(e.a_x, e.a_y, r.xs[i], r.ys[i]), r.tss[i] - e.a_ts)), true), 0) AS k))
SELECT mmsi, run_id, cut FROM w;

-- The reports before the seed, walked backwards from it by the same rule.
CREATE OR REPLACE TEMP TABLE pre_keep AS
SELECT mmsi, unnest(kept_rns) AS rn FROM (
  SELECT mmsi, list_reduce(elems, lambda acc, e:
    CASE WHEN acc.ts - e.ts > 0 AND reach(dist(acc.x, acc.y, e.x, e.y), acc.ts - e.ts)
         THEN {'ts': e.ts, 'x': e.x, 'y': e.y, 'kept': list_concat(acc.kept, e.kept)}
         ELSE acc END).kept AS kept_rns
  FROM (
    SELECT p.mmsi, list({'ts': p.ts, 'x': p.x, 'y': p.y,
                         'kept': CASE WHEN p.rn >= s.seed_rn THEN []::BIGINT[] ELSE [p.rn] END}
                        ORDER BY p.rn DESC) AS elems
    FROM pts p JOIN seed s USING (mmsi)
    WHERE p.rn <= s.seed_rn
    GROUP BY p.mmsi HAVING count(*) > 1))
WHERE len(kept_rns) > 0;

-- The clean points, cut into pieces at silences over 600 s.
CREATE OR REPLACE TEMP TABLE clean AS
SELECT *, sum(CASE WHEN ts - lag_ts > 600 THEN 1 ELSE 0 END)
            OVER (PARTITION BY mmsi ORDER BY t) AS seq
FROM (
  SELECT *, lag(ts) OVER (PARTITION BY mmsi ORDER BY t) AS lag_ts
  FROM (
    SELECT f.t, f.mmsi, f.lat, f.lon, f.x, f.y, f.sog, f.nav, f.ship_type, f.ts
    FROM fwd f JOIN walk w USING (mmsi, run_id)
    WHERE f.rn >= w.cut
    UNION ALL
    SELECT p.t, p.mmsi, p.lat, p.lon, p.x, p.y, p.sog, p.nav, p.ship_type, p.ts
    FROM pts p JOIN pre_keep k USING (mmsi, rn)));

-- The speed of each report: the window-centroid speed, or the reported SOG where the frames are
-- too short, in knots.
CREATE OR REPLACE TEMP TABLE speed AS
SELECT mmsi, seq, t, ts, sog,
  coalesce(
    CASE WHEN tf_max - ts >= 300 THEN dist(x, y, xf, yf) / (tf - ts)
         WHEN ts - tb_min >= 300 THEN dist(xb, yb, x, y) / (ts - tb) END * 3600 / 1852,
    sog) AS v
FROM (
  SELECT *,
    avg(x) OVER fw AS xf, avg(y) OVER fw AS yf, avg(ts) OVER fw AS tf, max(ts) OVER fw AS tf_max,
    avg(x) OVER bw AS xb, avg(y) OVER bw AS yb, avg(ts) OVER bw AS tb, min(ts) OVER bw AS tb_min
  FROM clean
  WINDOW fw AS (PARTITION BY mmsi, seq ORDER BY t
                RANGE BETWEEN CURRENT ROW AND INTERVAL 600 SECOND FOLLOWING),
         bw AS (PARTITION BY mmsi, seq ORDER BY t
                RANGE BETWEEN INTERVAL 600 SECOND PRECEDING AND CURRENT ROW));

-- The hysteresis, and the stopped runs lasting at least 300 s.
CREATE OR REPLACE TEMP TABLE stop_run AS
WITH mk AS (
  SELECT *, CASE WHEN v <= 0.2 THEN true WHEN v >= 0.5 THEN false END AS mark FROM speed),
g AS (SELECT *, count(mark) OVER (PARTITION BY mmsi, seq ORDER BY t) AS grp FROM mk),
s AS (
  SELECT *, coalesce(first_value(mark) OVER (PARTITION BY mmsi, seq, grp ORDER BY t), false)
    AS still
  FROM g),
i AS (
  SELECT *, sum(CASE WHEN still IS DISTINCT FROM prev_still THEN 1 ELSE 0 END)
    OVER (PARTITION BY mmsi, seq ORDER BY t) AS run
  FROM (SELECT *, lag(still) OVER (PARTITION BY mmsi, seq ORDER BY t) AS prev_still FROM s))
SELECT mmsi, seq, min(t) AS tstart, max(t) AS tend, count(*) AS npoints
FROM i WHERE still GROUP BY mmsi, seq, run
HAVING epoch(max(t)) - epoch(min(t)) >= 300;
