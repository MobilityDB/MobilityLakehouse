-- The Morton code the L0Z ordering sorts on.
--
-- Interleave the low 16 bits of two integers: the four shift-and-mask steps
-- spread each input bit to every second position, and the second argument is
-- offset by one so the two interleave. The masks are written in decimal
-- because a 0x literal is a parse error here.
--
-- The gridded centroid the two arguments carry is computed by the caller from
-- the month-wide extent, so this file holds no grid of its own and the same
-- macro serves any extent.
CREATE OR REPLACE MACRO spread16(v) AS (
  WITH a AS (SELECT (v & 65535)::BIGINT AS x),
       b AS (SELECT ((SELECT x FROM a) | ((SELECT x FROM a) << 8)) & 16711935 AS x),
       c AS (SELECT ((SELECT x FROM b) | ((SELECT x FROM b) << 4)) & 252645135 AS x),
       d AS (SELECT ((SELECT x FROM c) | ((SELECT x FROM c) << 2)) & 858993459 AS x),
       e AS (SELECT ((SELECT x FROM d) | ((SELECT x FROM d) << 1)) & 1431655765 AS x)
  SELECT x FROM e);

CREATE OR REPLACE MACRO morton(cx, cy) AS
  (spread16(cx) | (spread16(cy) << 1));
