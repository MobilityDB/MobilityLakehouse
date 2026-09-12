#!/usr/bin/env bash
# The three in-file-ordering layouts L0X, L0Z and L0H of the planar corpus, rewritten from its
# daily L0 files. Partition and columns are L0's; only the row order and the row-group size
# change, so a difference in what a query reads is attributable to the ordering alone.
#
# THE GRID IS COMPUTED ONCE OVER THE WHOLE PERIOD AND REUSED FOR EVERY DAY: a sort key is only
# meaningful against a fixed grid. THE EXTENT IS THE 1-99 PERCENTILE BAND OF THE SEGMENT
# CENTROIDS, not a min and max, because one vessel reporting mid ocean stretches a min/max extent
# across an empty map; positions outside the band are clamped to the edge cells, which costs them
# ordering precision and never correctness. ROW GROUPS HOLD 2048 ROWS, the smallest DuckDB
# writes: a request for 512 or 1024 also yields 2048.
#
#   RUN=data/stage/planar/2026-01-01_2026-02-01 benchmark/planar/40_order_layouts.sh [day ...]
#
# Environment: RUN (the run_clean.sh output directory); ROOT (the repository's data/), the tree
# holding log/ and tmp/; DUCKDB; LAYOUT_MEMORY (8GB). duckdb.sh and the macros are read from the
# directory holding this script.
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
L0="$RUN/L0"
OUT="$RUN/layouts_daily"
GRID="$RUN/grid.txt"
# The engine is $DUCKDB when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
export DUCKDB_ENGINE=${DUCKDB_ENGINE:-${DUCKDB:-}}
DUCKDB="$P/duckdb.sh"
MEM=${LAYOUT_MEMORY:-8GB}
MACROS="$P/layouts_order_macros.sql"
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL" "$OUT"/L0X "$OUT"/L0Z "$OUT"/L0H "$RUN/gen" "$ROOT/log"
trap 'rm -rf "$SPILL"' EXIT

# L0 holds one file per UTC day under the Hive year=/month= directories run_clean.sh writes.
ls "$L0"/year=*/month=*/day-*.parquet >/dev/null 2>&1 || { echo "no L0 day files under $L0" >&2; exit 1; }

if [ ! -s "$GRID" ]; then
  "$DUCKDB" -unsigned -noheader -list -c "
    SET memory_limit = '$MEM';
    SELECT format('{} {} {} {}',
      quantile_cont((trip_bbox.xmin + trip_bbox.xmax) / 2, 0.01),
      quantile_cont((trip_bbox.xmin + trip_bbox.xmax) / 2, 0.99),
      quantile_cont((trip_bbox.ymin + trip_bbox.ymax) / 2, 0.01),
      quantile_cont((trip_bbox.ymin + trip_bbox.ymax) / 2, 0.99))
    FROM read_parquet('$L0/year=*/month=*/day-*.parquet');" > "$GRID"
fi
read -r GX0 GX1 GY0 GY1 < "$GRID"
echo ">> grid x [$GX0, $GX1]  y [$GY0, $GY1]"

MX="(trip_bbox.xmin + trip_bbox.xmax) / 2"
MY="(trip_bbox.ymin + trip_bbox.ymax) / 2"
BOX="{'min_x':$GX0,'min_y':$GY0,'max_x':$GX1,'max_y':$GY1}::BOX_2D"
CX="least(65535, greatest(0, (($MX - $GX0) / ($GX1 - $GX0) * 65535.0)::BIGINT))"
CY="least(65535, greatest(0, (($MY - $GY0) / ($GY1 - $GY0) * 65535.0)::BIGINT))"
HX="least($GX1, greatest($GX0, $MX))"
HY="least($GY1, greatest($GY0, $MY))"

build_one() {
  local day="$1"
  local src
  src=$(ls "$L0"/year=*/month=*/day-"$day".parquet 2>/dev/null) || { echo "no L0 for $day" >&2; return 1; }
  local script="$RUN/gen/order-$day.sql"
  {
    echo "LOAD spatial;"
    echo "SET memory_limit = '$MEM';"
    echo "SET temp_directory = '$SPILL';"
    echo ".read $MACROS"
    # Each layout's COPY is timed on its own in the day's log, the build cost tab:eval-tradeoff reads
    echo ".timer on"
    echo "CREATE OR REPLACE TEMP TABLE src AS SELECT * FROM read_parquet('$src', hive_partitioning = false);"
    printf "COPY (SELECT * FROM src ORDER BY %s, %s, trip_tspan.tmin) TO '%s/L0X/day-%s.parquet' (FORMAT parquet, ROW_GROUP_SIZE 2048, COMPRESSION zstd);\n" \
      "$CX" "$CY" "$OUT" "$day"
    printf "COPY (SELECT * FROM src ORDER BY morton(%s, %s), trip_tspan.tmin) TO '%s/L0Z/day-%s.parquet' (FORMAT parquet, ROW_GROUP_SIZE 2048, COMPRESSION zstd);\n" \
      "$CX" "$CY" "$OUT" "$day"
    printf "COPY (SELECT * FROM src ORDER BY ST_Hilbert(%s, %s, %s), trip_tspan.tmin) TO '%s/L0H/day-%s.parquet' (FORMAT parquet, ROW_GROUP_SIZE 2048, COMPRESSION zstd);\n" \
      "$HX" "$HY" "$BOX" "$OUT" "$day"
  } > "$script"
  local t0
  t0=$(date +%s)
  "$DUCKDB" -unsigned -c ".read $script" > "$RUN/gen/order-$day.log" 2>&1
  printf 'ORDER\t%s\t%ss\tX=%s Z=%s H=%s bytes\n' "$day" "$(( $(date +%s) - t0 ))" \
    "$(stat -c %s "$OUT/L0X/day-$day.parquet")" \
    "$(stat -c %s "$OUT/L0Z/day-$day.parquet")" \
    "$(stat -c %s "$OUT/L0H/day-$day.parquet")" | tee -a "$ROOT/log/planar-order.tsv"
}

if [ "$#" -gt 0 ]; then
  for day in "$@"; do build_one "$day"; done
else
  for f in "$L0"/year=*/month=*/day-*.parquet; do
    day=$(basename "$f" .parquet); day=${day#day-}
    build_one "$day"
  done
fi
