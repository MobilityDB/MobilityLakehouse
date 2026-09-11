-- Stage 2b of the planar pipeline, run in the session of 20_clean.sql (tables `clean` and
-- `stop_run` in scope): the segments of each piece. Every stopped run is a stationary segment;
-- the moves are the stretches before the first stop, between two stops and after the last one,
-- each closed at both ends, so consecutive segments meet at one report; a piece with no stop is
-- one move. A segment carries its trajectory in EPSG:25832 and, from the same reports, its WGS84
-- twin, which is what an H3 cover reads (H3 takes geographic latitude and longitude only).
-- The table `segment` holds the result; run_clean.sh writes it out.

CREATE OR REPLACE TEMP TABLE seg_iv AS
WITH piece AS (SELECT mmsi, seq, min(t) AS p0, max(t) AS p1 FROM clean GROUP BY mmsi, seq),
st AS (
  SELECT s.*, lag(tend) OVER w AS prev_end, lead(tstart) OVER w AS next_start
  FROM stop_run s WINDOW w AS (PARTITION BY mmsi, seq ORDER BY tstart))
SELECT mmsi, seq, 'Stationary' AS segment_type, tstart AS t0, tend AS t1 FROM st
UNION ALL
SELECT st.mmsi, st.seq, 'In motion', coalesce(st.prev_end, p.p0), st.tstart
FROM st JOIN piece p USING (mmsi, seq) WHERE coalesce(st.prev_end, p.p0) < st.tstart
UNION ALL
SELECT st.mmsi, st.seq, 'In motion', st.tend, p.p1
FROM st JOIN piece p USING (mmsi, seq) WHERE st.next_start IS NULL AND st.tend < p.p1
UNION ALL
SELECT p.mmsi, p.seq, 'In motion', p.p0, p.p1
FROM piece p ANTI JOIN stop_run s USING (mmsi, seq);

CREATE OR REPLACE TEMP TABLE segment AS
SELECT i.mmsi, i.seq, i.segment_type, i.t0, i.t1, count(*) AS npoints,
  mode(c.ship_type) AS ship_type,
  asEWKB(setSRID(tgeompointSeq(list(tgeompoint(ST_Point(c.x, c.y), c.t) ORDER BY c.t)),
                 25832)) AS trip,
  asEWKB(setSRID(tgeompointSeq(list(tgeompoint(ST_Point(c.lon, c.lat), c.t) ORDER BY c.t)),
                 4326)) AS trip_wgs84
FROM seg_iv i
JOIN clean c ON c.mmsi = i.mmsi AND c.seq = i.seq AND c.t BETWEEN i.t0 AND i.t1
GROUP BY i.mmsi, i.seq, i.segment_type, i.t0, i.t1;
