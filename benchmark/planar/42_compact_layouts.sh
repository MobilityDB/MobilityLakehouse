#!/usr/bin/env bash
# The sorted-compact layouts L1s, L2s, L3s and L4s of the planar corpus: each partition of the daily
# layout L1..L4 (a hash shard, a tile, a time bin) gathered from its daily files into one monthly
# file, its rows sorted by the lexicographic key of L0X (the box centre's cell on the run's grid,
# then the start time), in row groups of 2048 rows, the smallest DuckDB writes. Compaction and the
# sort go together: merging the daily files alone widens each file's bounds, and the sort is what
# lets a query skip row groups inside the merged file. The partition value stays in the object key
# (Hive directories) and out of the file, as in the daily layout.
#
# ONE COPY PER PARTITION. A single `COPY (... ORDER BY ...) TO dir (PARTITION_BY ...)` over all
# partitions leaves runs out of order inside some partition files (measured on the probe days: 1-3
# breaks per layout, each a sorted run restarting near a file's end), while the same sorted query
# written for one partition keeps its order. Each COPY reads only its partition's daily files,
# since the filter on the Hive key prunes the others by path.
#
#   RUN=data/stage/planar/2026-01-01_2026-02-01 benchmark/planar/42_compact_layouts.sh [L1 L2 L3 L4]
#
# Environment: RUN (required); ROOT (the repository's data/), the tree holding log/ and tmp/;
# DUCKDB; LAYOUT_MEMORY (12GB); LAYOUT_THREADS (4).
set -euo pipefail

ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
# The engine is $DUCKDB when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
export DUCKDB_ENGINE=${DUCKDB_ENGINE:-${DUCKDB:-}}
DUCKDB="$(dirname "${BASH_SOURCE[0]}")/duckdb.sh"
# The query-ready zone carries a TemporalParquet footer, one document for every writer
TEMPORAL_FOOTER=$("$(dirname "${BASH_SOURCE[0]}")/temporal_footer.sh")
MEM=${LAYOUT_MEMORY:-12GB}
THREADS=${LAYOUT_THREADS:-4}
GRID="$RUN/grid.txt"
OUT="$RUN/layout_compact"
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL" "$OUT" "$RUN/gen" "$ROOT/log"
trap 'rm -rf "$SPILL"' EXIT

[ -s "$GRID" ] || { echo "no grid at $GRID: run 40_order_layouts.sh first" >&2; exit 1; }
read -r GX0 GX1 GY0 GY1 < "$GRID"

# The cell of the box centre on the run's grid, as 40_order_layouts.sh computes it for L0X
CX="least(65535, greatest(0, (((trip_xmin + trip_xmax) / 2 - $GX0) / ($GX1 - $GX0) * 65535.0)::BIGINT))"
CY="least(65535, greatest(0, (((trip_ymin + trip_ymax) / 2 - $GY0) / ($GY1 - $GY0) * 65535.0)::BIGINT))"

# The partition columns of each daily layout (41_part_layouts.sh), comma-separated
keys() {
  case "$1" in
    L1) echo "shard" ;;
    L2|L3) echo "cell_x,cell_y" ;;
    L4) echo "time_bin" ;;
    *) echo "unknown layout $1" >&2; return 1 ;;
  esac
}

for L in ${@:-L1 L2 L3 L4}; do
  k=$(keys "$L")
  dest="$OUT/${L}s"
  daily="$RUN/layouts_daily/$L/day-*/**/*.parquet"
  ls "$RUN"/layouts_daily/"$L"/day-*/ >/dev/null 2>&1 || { echo "no daily $L under $RUN" >&2; exit 1; }
  rm -rf "$dest"
  mkdir -p "$dest"

  # Each partition as its Hive path (key=value/...) and its SQL filter (key = value AND ...)
  parts=$("$DUCKDB" -unsigned -noheader -list -c "
    SELECT DISTINCT
      concat_ws('/', $(echo "$k" | sed "s/\([a-z_]*\)/'\1=' || \1/g")) || '|' ||
      concat_ws(' AND ', $(echo "$k" | sed "s/\([a-z_]*\)/'\1 = ' || \1/g"))
    FROM read_parquet('$daily', hive_partitioning = true) ORDER BY 1;")
  [ -n "$parts" ] || { echo "$L: no partitions found" >&2; exit 1; }

  script="$RUN/gen/compact-$L.sql"
  {
    echo "SET memory_limit = '$MEM';"
    echo "SET threads = $THREADS;"
    echo "SET temp_directory = '$SPILL';"
    echo "SET preserve_insertion_order = true;"
    # A multi-threaded COPY treats ROW_GROUP_SIZE as a target (row groups of up to 4,039 rows were
    # measured at 2048); one writer thread keeps every group within it.
    echo "SET threads = 1;"
    while IFS='|' read -r path filter; do
      mkdir -p "$dest/$path"
      echo "COPY (SELECT * EXCLUDE ($k) FROM read_parquet('$daily', hive_partitioning = true)"
      echo "      WHERE $filter ORDER BY $CX, $CY, trip_tmin)"
      echo "  TO '$dest/$path/data_0.parquet' (FORMAT parquet, ROW_GROUP_SIZE 2048, COMPRESSION zstd, $TEMPORAL_FOOTER);"
    done <<< "$parts"
  } > "$script"
  t0=$(date +%s)
  "$DUCKDB" -unsigned -c ".read $script" > "$RUN/gen/compact-$L.log" 2>&1 \
    || { echo "$L: compaction failed, see $RUN/gen/compact-$L.log" >&2; exit 1; }
  printf 'COMPACT\t%s\t%ss\t%s files\t%s bytes\n' "${L}s" "$(( $(date +%s) - t0 ))" \
    "$(find "$dest" -name '*.parquet' | wc -l)" "$(du -sb "$dest" | cut -f1)" \
    | tee -a "$ROOT/log/planar-compact.tsv"
done
