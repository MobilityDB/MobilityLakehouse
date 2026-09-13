#!/usr/bin/env bash
# Load the layouts of a run into DuckLake tables, one native INSERT per layout file, so each layout
# file becomes exactly one DuckLake data file holding the same rows in the same order, with the
# layout's row-group size (2048 for the sorted layouts L0X/L0Z/L0H and L1s-L4s, DuckDB's default
# otherwise), zstd, and no rows inlined into the catalog. DuckLake writes those files itself under
# s3://warehouse/ducklake/<namespace>/: its file pruning reads the statistics of files it wrote and
# skips none for files registered with ducklake_add_data_files (duckdb/ducklake issue #1442), so a
# native INSERT is what lets the catalog's pruning be measured. One DuckLake catalog per namespace,
# a DuckDB database file under $ROOT/lakehouse-store/ducklake/. Each layout is checked after
# loading: the catalog holds as many live files and rows as the layout.
#
#   RUN=<run> NAMESPACE=trips ./81_register_ducklake.sh L0 L0X ...
#
# Environment: RUN (required); ROOT (the repository's data/); NAMESPACE (trips); DUCKDB_ENGINE;
# DUCKLAKE_THREADS (4).
set -euo pipefail

ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
NS=${NAMESPACE:-trips}
# The engine is DUCKDB_ENGINE when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
DUCKDB="$(dirname "${BASH_SOURCE[0]}")/duckdb.sh"
THREADS=${DUCKLAKE_THREADS:-4}
META="$ROOT/lakehouse-store/ducklake/$NS.ducklake"
mkdir -p "$(dirname "$META")" "$RUN/gen" "$ROOT/log"

SETUP="LOAD httpfs; LOAD ducklake;
CREATE SECRET (TYPE s3, KEY_ID 'admin', SECRET 'password', ENDPOINT 'localhost:9000', URL_STYLE 'path', USE_SSL false, REGION 'us-east-1');
ATTACH 'ducklake:$META' AS dl (DATA_PATH 's3://warehouse/ducklake/$NS/');"

# The layout's files relative to the run, as 70_queries.py reads them
layout_files() {
  case "$1" in
    L0)          (cd "$RUN" && ls L0/year=*/month=*/day-*.parquet) ;;
    L0X|L0Z|L0H) (cd "$RUN" && ls layouts_daily/"$1"/day-*.parquet) ;;
    L1|L2|L3|L4) (cd "$RUN" && find layouts_daily/"$1" -name '*.parquet' | sort) ;;
    L1s|L2s|L3s|L4s) (cd "$RUN" && find layout_compact/"$1" -name '*.parquet' | sort) ;;
    *) echo "unknown layout $1" >&2; return 1 ;;
  esac
}

# The row-group size the layout was written with
row_group_size() {
  case "$1" in
    L0X|L0Z|L0H|L1s|L2s|L3s|L4s) echo 2048 ;;
    *) echo 122880 ;;
  esac
}

for L in "$@"; do
  t=$(echo "$L" | tr '[:upper:]' '[:lower:]')
  files=$(layout_files "$L")
  [ -n "$files" ] || { echo "$L: no files under $RUN" >&2; exit 1; }
  # The first line by expansion: `head -1` on a list longer than a pipe buffer closes the pipe
  # under its writer, which pipefail turns into the script's exit status 141
  first=${files%%$'\n'*}
  nfiles=$(echo "$files" | wc -l)
  script="$RUN/gen/ducklake-$NS-$L.sql"
  {
    echo "$SETUP"
    echo "SET threads = $THREADS; SET preserve_insertion_order = true;"
    echo "CALL ducklake_set_option('dl', 'parquet_row_group_size', $(row_group_size "$L"));"
    echo "CALL ducklake_set_option('dl', 'parquet_compression', 'zstd');"
    echo "CALL ducklake_set_option('dl', 'data_inlining_row_limit', 0);"
    echo "DROP TABLE IF EXISTS dl.$t;"
    echo "CREATE TABLE dl.$t AS SELECT * FROM read_parquet('$RUN/$first', hive_partitioning = false) LIMIT 0;"
    for f in $files; do
      echo "INSERT INTO dl.$t SELECT * FROM read_parquet('$RUN/$f', hive_partitioning = false);"
    done
  } > "$script"
  t0=$(date +%s)
  "$DUCKDB" -unsigned -noheader -list -c ".read $script" > "$RUN/gen/ducklake-$NS-$L.log" 2>&1 \
    || { echo "$L: load failed, see $RUN/gen/ducklake-$NS-$L.log" >&2; exit 1; }

  got=$("$DUCKDB" -unsigned -noheader -list -c "$SETUP
    SELECT count(*) || ' ' || coalesce(sum(f.record_count), 0)
    FROM __ducklake_metadata_dl.ducklake_data_file f
    JOIN __ducklake_metadata_dl.ducklake_table tb USING (table_id)
    WHERE tb.table_name = '$t' AND tb.end_snapshot IS NULL AND f.end_snapshot IS NULL;" | tail -1)
  # The statement travels on standard input: a month layout's file list passed as one argument
  # exceeds the length the kernel allows it
  want="$nfiles $(echo "SELECT count(*) FROM read_parquet([$(echo "$files" | sed "s|.*|'$RUN/&'|" | paste -sd,)], hive_partitioning = false);" \
    | "$DUCKDB" -unsigned -noheader -list)"
  [ "$got" = "$want" ] || { echo "$L: catalog holds [$got] files/rows, the layout [$want]" >&2; exit 1; }
  printf 'DUCKLAKE\t%s\t%s\t%ss\t%s files %s rows\n' "$NS" "$L" "$(( $(date +%s) - t0 ))" $got \
    | tee -a "$ROOT/log/planar-ducklake.tsv"
done
ls -la "$META"
