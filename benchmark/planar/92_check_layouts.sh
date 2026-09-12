#!/usr/bin/env bash
# The checks every layout of a run passes before a figure is read from it, each printing what it
# examined beside what broke:
#   schema      exactly L0's columns and types (the trips schema), so no Hive path key or
#               partition column leaks into a file;
#   rows        a layout that never cuts a trajectory (L0X, L0Z, L0H, L1, L1s) holds L0's rows,
#               and a compact layout holds its daily layout's rows (L2s = L2, L3s = L3, L4s = L4);
#   row groups  the sorted layouts (L0X, L0Z, L0H, L1s..L4s) write groups of at most 2048 rows;
#   order       the lexicographically sorted files (L0X, L1s..L4s) have no row whose (cell x,
#               cell y, start time) key is below the row before it.
# Exits non-zero on any failure.
#
#   RUN=~/ais-lakehouse/stage/planar/2026-01-01_2026-02-01 ./92_check_layouts.sh
#
# Environment: RUN (required); DUCKDB; CHECK_MEMORY (8GB).
set -euo pipefail

RUN=${RUN:?RUN is required}
# The engine is $DUCKDB when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
export DUCKDB_ENGINE=${DUCKDB_ENGINE:-${DUCKDB:-}}
DUCKDB="$(dirname "${BASH_SOURCE[0]}")/duckdb.sh"
MEM=${CHECK_MEMORY:-8GB}
GRID="$RUN/grid.txt"
[ -s "$GRID" ] || { echo "no grid at $GRID: run 40_order_layouts.sh first" >&2; exit 1; }
read -r GX0 GX1 GY0 GY1 < "$GRID"
CX="least(65535, greatest(0, (((trip_xmin + trip_xmax) / 2 - $GX0) / ($GX1 - $GX0) * 65535.0)::BIGINT))"
CY="least(65535, greatest(0, (((trip_ymin + trip_ymax) / 2 - $GY0) / ($GY1 - $GY0) * 65535.0)::BIGINT))"

layout_glob() {
  case "$1" in
    L0)          echo "$RUN/L0/year=*/month=*/day-*.parquet" ;;
    L0X|L0Z|L0H) echo "$RUN/layouts_daily/$1/day-*.parquet" ;;
    L1|L2|L3|L4) echo "$RUN/layouts_daily/$1/day-*/**/*.parquet" ;;
    L1s|L2s|L3s|L4s) echo "$RUN/layout_compact/$1/**/*.parquet" ;;
  esac
}

q() { "$DUCKDB" -unsigned -noheader -list -c "SET memory_limit = '$MEM'; $1" | tail -1; }
schema() { q "SELECT string_agg(column_name || ' ' || column_type, ', ' ORDER BY column_name) FROM (DESCRIBE SELECT * FROM read_parquet('$1', hive_partitioning = false));"; }
rows() { q "SELECT count(*) FROM read_parquet('$1', hive_partitioning = false);"; }

want_schema=$(schema "$(layout_glob L0)")
declare -A nrows
nrows[L0]=$(rows "$(layout_glob L0)")
fail=0
report() { printf '%-4s %-11s %s\n' "$1" "$2" "$3"; [ "$4" = ok ] || fail=1; }

for L in L0X L0Z L0H L1 L2 L3 L4 L1s L2s L3s L4s; do
  g=$(layout_glob "$L")
  case "$L" in
    L0X|L0Z|L0H|L1|L2|L3|L4) dir="$RUN/layouts_daily/$L" ;;
    *) dir="$RUN/layout_compact/$L" ;;
  esac
  [ -n "$(find "$dir" -name '*.parquet' -print -quit 2>/dev/null)" ] \
    || { report "$L" absent "no files" ok; continue; }
  s=$(schema "$g")
  [ "$s" = "$want_schema" ] && report "$L" schema "trips schema" ok \
    || report "$L" schema "DIFFERS: $s" fail
  nrows[$L]=$(rows "$g")
  # A stale file left beside a rewritten one duplicates rows, which no answer count can see.
  d=$(q "SELECT count(*) - count(DISTINCT (MMSI, trip_tmin, trip_tmax, trip_xmin, trip_ymin, md5(trip)))
         FROM read_parquet('$g', hive_partitioning = false);")
  [ "$d" = 0 ] && report "$L" duplicates "0" ok || report "$L" duplicates "$d duplicate rows" fail
  case "$L" in
    L0X|L0Z|L0H|L1|L1s) ref=L0 ;;
    L2s|L3s|L4s) ref=${L%s} ;;
    *) ref= ;;
  esac
  if [ -n "$ref" ]; then
    [ "${nrows[$L]}" = "${nrows[$ref]:-}" ] && report "$L" rows "${nrows[$L]} = $ref" ok \
      || report "$L" rows "${nrows[$L]} != $ref ${nrows[$ref]:-?}" fail
  else
    report "$L" rows "${nrows[$L]}" ok
  fi
  case "$L" in
    L0X|L0Z|L0H|L1s|L2s|L3s|L4s)
      m=$(q "SELECT max(row_group_num_rows) FROM parquet_metadata('$g');")
      [ "$m" -le 2048 ] && report "$L" row-groups "max $m rows" ok \
        || report "$L" row-groups "max $m rows > 2048" fail ;;
  esac
  case "$L" in
    L0X|L1s|L2s|L3s|L4s)
      b=$(q "SELECT count(*) FILTER (WHERE pcx > cx OR (pcx = cx AND pcy > cy) OR (pcx = cx AND pcy = cy AND pt > t))
             FROM (SELECT cx, cy, t, lag(cx) OVER w AS pcx, lag(cy) OVER w AS pcy, lag(t) OVER w AS pt
                   FROM (SELECT filename, file_row_number AS rn, $CX AS cx, $CY AS cy, trip_tmin AS t
                         FROM read_parquet('$g', hive_partitioning = false, filename = true, file_row_number = true))
                   WINDOW w AS (PARTITION BY filename ORDER BY rn));")
      [ "$b" = 0 ] && report "$L" order "0 breaks" ok || report "$L" order "$b breaks" fail ;;
  esac
done
[ "$fail" = 0 ] && echo "ALL LAYOUT CHECKS PASS" || { echo "LAYOUT CHECKS FAIL" >&2; exit 1; }
