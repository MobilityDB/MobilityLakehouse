#!/usr/bin/env bash
# What each layout lets a query SKIP: per (layout, window), the row groups, files, rows and bytes
# whose stored box the window admits.
#
# THE PRUNING ORACLE IS THE PARQUET STATISTICS, NOT A SCAN OF THE DATA. `parquet_metadata()`
# reads each row group's per-field min/max out of the file footer, which is what the engine
# consults before deciding to open it; the covering columns are the struct fields `trip_bbox`
# {xmin, ymin, xmax, ymax} and `trip_tspan` {tmin, tmax}, whose statistics Parquet names
# `trip_bbox, xmin` and so on. A ROW GROUP IS ADMITTED IFF ITS BOX MEETS THE WINDOW IN x, y AND
# TIME: disagreement in any one dimension proves the group cannot contribute.
#
# The windows are the rectangles of windows_25832.csv (45_windows.sql), in the corpus's own CRS,
# so no projection enters the comparison.
#
#   RUN=<run> ./50_query_layouts.sh
#
# Environment: RUN (required); ROOT (the repository's data/); DUCKDB_ENGINE; LAYOUTS (all
# twelve); LAYOUT_PRUNING_OUT (the table appended to, $ROOT/results/planar/layout-pruning.csv).
set -euo pipefail

ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
# The engine is DUCKDB_ENGINE when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
DUCKDB="$(dirname "${BASH_SOURCE[0]}")/duckdb.sh"
WINDOWS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/windows_25832.csv"
LAYOUTS=${LAYOUTS:-"L0 L0X L0Z L0H L1 L2 L3 L4 L1s L2s L3s L4s"}
OUT=${LAYOUT_PRUNING_OUT:-$ROOT/results/planar/layout-pruning.csv}
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL" "$(dirname "$OUT")" "$ROOT/log"
trap 'rm -rf "$SPILL"' EXIT

[ -s "$WINDOWS" ] || { echo "no windows at $WINDOWS" >&2; exit 1; }

layout_glob() {
  case "$1" in
    L0)          echo "$RUN/L0/year=*/month=*/day-*.parquet" ;;
    L0X|L0Z|L0H) echo "$RUN/layouts_daily/$1/day-*.parquet" ;;
    L1|L2|L3|L4) echo "$RUN/layouts_daily/$1/day-*/**/*.parquet" ;;
    L1s|L2s|L3s|L4s) echo "$RUN/layout_compact/$1/**/*.parquet" ;;
    *) echo "unknown layout $1" >&2; return 1 ;;
  esac
}

[ -s "$OUT" ] || echo "layout,window,rg_read,rg_total,files_read,files_total,rows_read,rows_total,bytes_read,bytes_total,pct_bytes,pct_rows" > "$OUT"

for L in $LAYOUTS; do
  glob=$(layout_glob "$L")
  "$DUCKDB" -unsigned -noheader -list -c "
SET temp_directory = '$SPILL';
CREATE OR REPLACE TEMP TABLE rg AS
SELECT file_name, row_group_id,
  any_value(row_group_num_rows) AS nrows, any_value(row_group_bytes) AS nbytes,
  min(CASE WHEN path_in_schema = 'trip_bbox, xmin' THEN stats_min::DOUBLE END) AS bx0,
  max(CASE WHEN path_in_schema = 'trip_bbox, xmax' THEN stats_max::DOUBLE END) AS bx1,
  min(CASE WHEN path_in_schema = 'trip_bbox, ymin' THEN stats_min::DOUBLE END) AS by0,
  max(CASE WHEN path_in_schema = 'trip_bbox, ymax' THEN stats_max::DOUBLE END) AS by1,
  min(CASE WHEN path_in_schema = 'trip_tspan, tmin' THEN stats_min::TIMESTAMP END) AS bt0,
  max(CASE WHEN path_in_schema = 'trip_tspan, tmax' THEN stats_max::TIMESTAMP END) AS bt1
FROM parquet_metadata('$glob')
GROUP BY file_name, row_group_id;
CREATE OR REPLACE TEMP TABLE win AS
SELECT column0 AS name, column1 AS qx0, column2 AS qy0, column3 AS qx1, column4 AS qy1,
  column5::TIMESTAMP AS qt0, column6::TIMESTAMP AS qt1
FROM read_csv('$WINDOWS', header = false, all_varchar = false);
SELECT '$L' || ',' || name || ',' || rg_read || ',' || rg_total || ',' || files_read || ',' ||
  files_total || ',' || rows_read || ',' || rows_total || ',' || bytes_read || ',' ||
  bytes_total || ',' || round(100.0 * bytes_read / bytes_total, 3) || ',' ||
  round(100.0 * rows_read / rows_total, 3)
FROM (
  SELECT w.name,
    count(*) FILTER (WHERE adm) AS rg_read, count(*) AS rg_total,
    count(DISTINCT file_name) FILTER (WHERE adm) AS files_read,
    count(DISTINCT file_name) AS files_total,
    coalesce(sum(nrows) FILTER (WHERE adm), 0) AS rows_read, sum(nrows) AS rows_total,
    coalesce(sum(nbytes) FILTER (WHERE adm), 0) AS bytes_read, sum(nbytes) AS bytes_total
  FROM win w, LATERAL (
    SELECT nrows, nbytes, file_name,
      (bx1 >= w.qx0 AND bx0 <= w.qx1 AND by1 >= w.qy0 AND by0 <= w.qy1
       AND bt1 >= w.qt0 AND bt0 <= w.qt1) AS adm
    FROM rg)
  GROUP BY w.name)
ORDER BY name;" 2>>"$ROOT/log/planar-query-errors.log" | tee -a "$OUT"
done
