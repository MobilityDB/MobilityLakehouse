#!/usr/bin/env bash
# A layout's moving rows as the cell-cover harness reads them, in WGS84: one line per position,
# trip_id,lon,lat,unix_seconds, the positions of a row contiguous and in time order. A row is one
# 'In motion' row of the layout's files, so a move crossing midnight is two rows of L0, as a query
# over the files sees it, and its positions are `transform(trip, 4326)`, every instant of the
# stored trajectory carried to EPSG:4326, the frame H3 reads. The exact predicate and the covers
# both read these trips. A sidecar gives each row's covering bounds as the layout stores them,
# trip_id,xmin,ymin,xmax,ymax,tmin_unix,tmax_unix in the corpus's metric frame, which is what the
# box filter of a query reads.
#
# The export is checked on the file the harness reads: it holds one line per instant of every
# moving row, then trips_pack refuses a file whose trip identifiers decrease or whose time does
# not increase within a trip.
#
#   RUN=<run> [LAYOUT=L3s] ./46_trips.sh
#
# L0 writes trips.csv, trips.bin and trips-box.csv in the run; another layout trips-<layout>.csv,
# .bin and -box.csv.
#
# Environment: RUN (required); ROOT (the repository's data/); LAYOUT (L0); DUCKDB_ENGINE;
# TRIPS_MEMORY (10GB).
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
LAYOUT=${LAYOUT:-L0}
case "$LAYOUT" in
  L0)          DIR="$RUN/L0"; FILES="$DIR/year=*/month=*/day-*.parquet"; BASE=trips ;;
  L0X|L0Z|L0H) DIR="$RUN/layouts_daily/$LAYOUT"; FILES="$DIR/day-*.parquet"; BASE="trips-$LAYOUT" ;;
  L1|L2|L3|L4) DIR="$RUN/layouts_daily/$LAYOUT"; FILES="$DIR/day-*/**/*.parquet"; BASE="trips-$LAYOUT" ;;
  L1s|L2s|L3s|L4s) DIR="$RUN/layout_compact/$LAYOUT"; FILES="$DIR/**/*.parquet"; BASE="trips-$LAYOUT" ;;
  *) echo "unknown layout $LAYOUT" >&2; exit 1 ;;
esac
# The engine is DUCKDB_ENGINE when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
DUCKDB="$P/duckdb.sh"
PACK="$P/cellcover/trips_pack"
MEM=${TRIPS_MEMORY:-10GB}
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL" "$RUN/gen"
trap 'rm -rf "$SPILL"' EXIT

[ -x "$PACK" ] || { echo "no packer at $PACK; run planar/cellcover/build.sh <MEOS prefix>" >&2; exit 1; }
SRC="read_parquet('$FILES', filename = true, file_row_number = true, hive_partitioning = false)"

cat > "$RUN/gen/$BASE.sql" <<SQL
LOAD spatial;
SET memory_limit = '$MEM';
SET temp_directory = '$SPILL';
SET preserve_insertion_order = true;
CREATE OR REPLACE TEMP TABLE m AS
  SELECT row_number() OVER (ORDER BY filename, file_row_number) AS trip_id, trip,
    trip_xmin, trip_ymin, trip_xmax, trip_ymax, trip_tmin, trip_tmax
  FROM $SRC
  WHERE segment_type = 'In motion';
COPY (
  WITH z AS (
    SELECT trip_id, unnest(instants(transform(tgeompointFromEWKB(trip), 4326))) AS iw
    FROM m)
  SELECT trip_id, ST_X(getValue(iw)), ST_Y(getValue(iw)), epoch(getTimestamp(iw))::BIGINT
  FROM z ORDER BY trip_id, getTimestamp(iw)
) TO '$RUN/$BASE.csv' (FORMAT csv, HEADER false);
COPY (
  SELECT trip_id, trip_xmin, trip_ymin, trip_xmax, trip_ymax, floor(epoch(trip_tmin))::BIGINT,
    ceil(epoch(trip_tmax))::BIGINT
  FROM m ORDER BY trip_id
) TO '$RUN/$BASE-box.csv' (FORMAT csv, HEADER false);
SQL

# The files are found by walking the layout's directory: a Hive layout nests them deeper than the
# shell's `**`, which matches one directory level, reaches
[ -n "$(find "$DIR" -name '*.parquet' -print -quit 2>/dev/null)" ] \
  || { echo "no $LAYOUT files under $DIR" >&2; exit 1; }
t0=$(date +%s)
"$DUCKDB" -unsigned -c ".read $RUN/gen/$BASE.sql" > "$RUN/gen/$BASE.log" 2>&1
echo ">> $BASE.csv written in $(( $(date +%s) - t0 )) s"

"$DUCKDB" -unsigned -noheader -list -c "
  SET memory_limit = '$MEM';
  SET temp_directory = '$SPILL';
  SELECT format('moving $LAYOUT rows {}, of them with two instants or more {}',
    count(*), count(*) FILTER (WHERE numInstants(tgeompointFromEWKB(trip)) >= 2))
  FROM $SRC WHERE segment_type = 'In motion';"

want=$("$DUCKDB" -unsigned -noheader -list -c "
  SET memory_limit = '$MEM';
  SET temp_directory = '$SPILL';
  SELECT sum(numInstants(tgeompointFromEWKB(trip)))
  FROM $SRC WHERE segment_type = 'In motion';")
got=$(wc -l < "$RUN/$BASE.csv")
[ "$got" = "$want" ] || { echo "$BASE.csv holds $got positions, the moving rows $want" >&2; exit 1; }
echo ">> $BASE.csv: $got positions, one per instant of every moving row"

"$PACK" "$RUN/$BASE.csv" "$RUN/$BASE.bin"
ls -la --time-style='+%F %T' "$RUN/$BASE.csv" "$RUN/$BASE.bin" "$RUN/$BASE-box.csv"
