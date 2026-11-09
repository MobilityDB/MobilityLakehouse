#!/usr/bin/env bash
# The point baseline of the planar corpus, GP: one row per position of L0, the representation most
# lakehouses hold, as GeoParquet 1.1 with its bounding-box covering column, rewritten from the
# daily L0 files.
#
# THE POSITIONS ARE L0'S OWN INSTANTS, so the table holds what the layouts hold and nothing they
# do not: each segment's instants unnested, one row per vessel and instant (a segment cut at a stop
# or at midnight shares its boundary instant with the next piece, and that instant is kept once,
# in the day it falls on). A row is the vessel, its ship type, the instant as a UTC TIMESTAMP `t`,
# the point as `geometry` and its covering `bbox` {xmin, ymin, xmax, ymax}, and the day `dt`.
#
# THE ORDER AND THE ROW GROUPS ARE L0H'S: each day is sorted by the Hilbert index of the point on
# the run's grid (grid.txt, the extent 40_order_layouts.sh computes) and then by time, in row
# groups of 2048 rows, so a query prunes the point table's row groups on the covering as it prunes
# L0H's on the stored boxes, and the comparison is between representations, not orderings. The
# `geo` document is written whole (GEOPARQUET_VERSION 'NONE' with KV_METADATA), since the engine's
# own GeoParquet metadata states no covering.
#
#   RUN=data/stage/planar/2026-01-01_2026-02-01 benchmark/planar/48_points.sh [day ...]
#
# Environment: RUN (the run_clean.sh output directory); ROOT (the repository's data/), the tree
# holding log/ and tmp/; DUCKDB; LAYOUT_MEMORY (8GB). duckdb.sh is read from the directory holding
# this script.
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
L0="$RUN/L0"
OUT="$RUN/points"
GRID="$RUN/grid.txt"
# The engine is $DUCKDB when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled and the session time zone at UTC.
export DUCKDB_ENGINE=${DUCKDB_ENGINE:-${DUCKDB:-}}
DUCKDB="$P/duckdb.sh"
MEM=${LAYOUT_MEMORY:-8GB}
SRID=${SRID:-25832}
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL" "$OUT" "$RUN/gen" "$ROOT/log"
trap 'rm -rf "$SPILL"' EXIT

ls "$L0"/year=*/month=*/day-*.parquet >/dev/null 2>&1 || { echo "no L0 day files under $L0" >&2; exit 1; }
[ -s "$GRID" ] || { echo "no grid at $GRID: 40_order_layouts.sh computes it" >&2; exit 1; }
read -r GX0 GX1 GY0 GY1 < "$GRID"
BOX="{'min_x':$GX0,'min_y':$GY0,'max_x':$GX1,'max_y':$GY1}::BOX_2D"
GEO='{"version":"1.1.0","primary_column":"geometry","columns":{"geometry":{"encoding":"WKB","geometry_types":["Point"],"crs":{"id":{"authority":"EPSG","code":'$SRID'}},"covering":{"bbox":{"xmin":["bbox","xmin"],"ymin":["bbox","ymin"],"xmax":["bbox","xmax"],"ymax":["bbox","ymax"]}}}}}'

build_one() {
  local day="$1"
  local src
  src=$(ls "$L0"/year=*/month=*/day-"$day".parquet 2>/dev/null) || { echo "no L0 for $day" >&2; return 1; }
  local script="$RUN/gen/points-$day.sql"
  cat > "$script" <<SQL
LOAD spatial;
SET memory_limit = '$MEM';
SET temp_directory = '$SPILL';
SET preserve_insertion_order = false;
.timer on
CREATE OR REPLACE TEMP TABLE pts AS
SELECT DISTINCT ON (MMSI, t) MMSI, ship_type, t, x, y
FROM (SELECT MMSI, ship_type, getTimestamp(i)::TIMESTAMP AS t, ST_X(getValue(i)) AS x,
             ST_Y(getValue(i)) AS y
      FROM (SELECT MMSI, ship_type, unnest(instants(tgeompointFromEWKB(trip))) AS i
            FROM read_parquet('$src', hive_partitioning = false)))
WHERE t >= TIMESTAMP '$day 00:00:00' AND t < TIMESTAMP '$day 00:00:00' + INTERVAL 1 DAY;
SET threads = 1;
COPY (SELECT MMSI, ship_type, t, ST_Point(x, y) AS geometry,
             {'xmin': x, 'ymin': y, 'xmax': x, 'ymax': y} AS bbox, DATE '$day' AS dt
      FROM pts
      ORDER BY ST_Hilbert(least($GX1, greatest($GX0, x)), least($GY1, greatest($GY0, y)), $BOX), t)
  TO '$OUT/day-$day.parquet'
  (FORMAT parquet, ROW_GROUP_SIZE 2048, COMPRESSION zstd, GEOPARQUET_VERSION 'NONE',
   KV_METADATA {geo: '$GEO'});
SELECT count(*) AS points FROM pts;
SQL
  local t0
  t0=$(date +%s)
  "$DUCKDB" -unsigned -c ".read $script" > "$RUN/gen/points-$day.log" 2>&1
  printf 'POINTS\t%s\t%ss\t%s bytes\n' "$day" "$(( $(date +%s) - t0 ))" \
    "$(stat -c %s "$OUT/day-$day.parquet")" | tee -a "$ROOT/log/planar-points.tsv"
}

if [ "$#" -gt 0 ]; then
  for day in "$@"; do build_one "$day"; done
else
  for f in "$L0"/year=*/month=*/day-*.parquet; do
    day=$(basename "$f" .parquet); day=${day#day-}
    build_one "$day"
  done
fi
