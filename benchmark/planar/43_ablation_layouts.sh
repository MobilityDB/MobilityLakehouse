#!/usr/bin/env bash
# The ablation layouts of the planar corpus, which change one factor at a time where the twelve
# layouts change several together:
#
#   L0r          each L0 day in its own row order, in row groups of 2048 rows: against L0 the
#                row-group size alone, against L0X, L0Z and L0H the sort alone;
#   L1c..L4c     the daily files of each partition gathered into one file per month in their row
#                order, in row groups of 122880 rows (DuckDB's default): against L1..L4 compaction
#                alone;
#   L1cr..L4cr   the same monthly files in row groups of 2048 rows: against L1c..L4c the row-group
#                size, against L1s..L4s the sort;
#   L2d, L3d     each daily file of L2 and L3 sorted by L0X's key (the box centre's cell on the
#                run's grid, then the start time), in row groups of 2048 rows: against L2 and L3
#                the sort without compaction.
#
# Every unsorted write runs on one thread with the insertion order preserved, so a file keeps the
# rows of its source in the source's order; every write of 2048-row groups runs on one thread,
# since a multi-threaded COPY treats ROW_GROUP_SIZE as a target. The monthly layouts are the COPYs
# of 42_compact_layouts.sh without its sort; L0r and L2d/L3d are per-file COPYs written as
# #build_one of 40_order_layouts.sh writes L0X.
#
#   RUN=data/stage/planar/2026-01-01_2026-02-01 benchmark/planar/43_ablation_layouts.sh [L0r L1c L1cr L2d ...]
#
# Environment: RUN (required); ROOT (the repository's data/), the tree holding log/ and tmp/;
# DUCKDB; LAYOUT_MEMORY (12GB).
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
export ROOT RUN
# The engine is $DUCKDB when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
export DUCKDB_ENGINE=${DUCKDB_ENGINE:-${DUCKDB:-}}
DUCKDB="$P/duckdb.sh"
# The query-ready zone carries a TemporalParquet footer, one document for every writer
TEMPORAL_FOOTER=$("$P/temporal_footer.sh")
MEM=${LAYOUT_MEMORY:-12GB}
export LAYOUT_MEMORY=$MEM
L0="$RUN/L0"
OUT="$RUN/layouts_daily"
GRID="$RUN/grid.txt"
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL" "$RUN/gen" "$ROOT/log"
trap 'rm -rf "$SPILL"' EXIT

[ -s "$GRID" ] || { echo "no grid at $GRID: run 40_order_layouts.sh first" >&2; exit 1; }
read -r GX0 GX1 GY0 GY1 < "$GRID"

# The cell of the box centre on the run's grid, as 42_compact_layouts.sh computes it on the trips schema
CX="least(65535, greatest(0, (((trip_xmin + trip_xmax) / 2 - $GX0) / ($GX1 - $GX0) * 65535.0)::BIGINT))"
CY="least(65535, greatest(0, (((trip_ymin + trip_ymax) / 2 - $GY0) / ($GY1 - $GY0) * 65535.0)::BIGINT))"

# The settings every generated script opens with: one writer thread, insertion order kept
header() {
  echo "SET memory_limit = '$MEM';"
  echo "SET temp_directory = '$SPILL';"
  echo "SET preserve_insertion_order = true;"
  echo "SET threads = 1;"
}

# Runs a generated script and logs the layout's build time, files and bytes, as the COMPACT line of
# 42_compact_layouts.sh does
run() {
  local name="$1" script="$2" dir="$3" t0
  t0=$(date +%s)
  "$DUCKDB" -unsigned -c ".read $script" > "${script%.sql}.log" 2>&1 \
    || { echo "$name: build failed, see ${script%.sql}.log" >&2; exit 1; }
  printf 'ABLATION\t%s\t%ss\t%s files\t%s bytes\n' "$name" "$(( $(date +%s) - t0 ))" \
    "$(find "$dir" -name '*.parquet' | wc -l)" "$(du -sb "$dir" | cut -f1)" \
    | tee -a "$ROOT/log/planar-ablation.tsv"
}

# L0r: each L0 day copied in its row order, under the day's name as #build_one of
# 40_order_layouts.sh names L0X's
build_l0r() {
  ls "$L0"/year=*/month=*/day-*.parquet >/dev/null 2>&1 || { echo "no L0 day files under $L0" >&2; exit 1; }
  local dest="$OUT/L0r" script="$RUN/gen/ablation-L0r.sql" f day
  rm -rf "$dest"; mkdir -p "$dest"
  {
    header
    for f in "$L0"/year=*/month=*/day-*.parquet; do
      day=$(basename "$f" .parquet)
      echo "COPY (SELECT * FROM read_parquet('$f', hive_partitioning = false))"
      echo "  TO '$dest/$day.parquet' (FORMAT parquet, ROW_GROUP_SIZE 2048, COMPRESSION zstd, $TEMPORAL_FOOTER);"
    done
  } > "$script"
  run L0r "$script" "$dest"
}

# L2d, L3d: every daily file of L2 or L3 sorted on its own by L0X's key, at the same path under
# the new layout, so the partition value stays in the object key as in the daily layout
build_daily_sorted() {
  local L="$1" src="$OUT/$1" dest="$OUT/$1d" script="$RUN/gen/ablation-$1d.sql" f rel
  ls "$src"/day-*/ >/dev/null 2>&1 || { echo "no daily $L under $RUN" >&2; exit 1; }
  rm -rf "$dest"
  {
    header
    while IFS= read -r f; do
      rel=${f#"$src"/}
      mkdir -p "$dest/$(dirname "$rel")"
      echo "COPY (SELECT * FROM read_parquet('$f', hive_partitioning = false) ORDER BY $CX, $CY, trip_tmin)"
      echo "  TO '$dest/$rel' (FORMAT parquet, ROW_GROUP_SIZE 2048, COMPRESSION zstd, $TEMPORAL_FOOTER);"
    done < <(find "$src" -name '*.parquet' | sort)
  } > "$script"
  run "${L}d" "$script" "$dest"
}

for A in ${@:-L0r L1c L2c L3c L4c L1cr L2cr L3cr L4cr L2d L3d}; do
  case "$A" in
    L0r) build_l0r ;;
    L[1-4]c) COMPACT_SORT=0 COMPACT_ROW_GROUP=122880 COMPACT_SUFFIX=c "$P/42_compact_layouts.sh" "${A%c}" ;;
    L[1-4]cr) COMPACT_SORT=0 COMPACT_ROW_GROUP=2048 COMPACT_SUFFIX=cr "$P/42_compact_layouts.sh" "${A%cr}" ;;
    L2d|L3d) build_daily_sorted "${A%d}" ;;
    *) echo "unknown ablation layout $A" >&2; exit 1 ;;
  esac
done
