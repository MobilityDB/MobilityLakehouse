#!/usr/bin/env bash
# The planar cleaning, segmentation and L0 of the lakehouse corpus over [LO, HI).
#
# Stage 1 (10_stage.sql) reads the raw zone once and writes the vessel buckets. Stage 2
# (20_clean.sql, 25_segments.sql) cleans and segments each bucket over its whole period and writes
# its clean points, stopped runs and segments; a vessel's reports all land in one bucket, so no
# rule ever sees a day or bucket seam. Stage 3 (30_l0.sql) writes one L0 file per calendar day and
# checks it from the written file. Each stage logs its duration; every DuckDB process spills into a
# directory of its own, since DuckDB names spill files by size class and index alone.
#
#   LO=2026-01-20 HI=2026-01-22 ./run_clean.sh
#
# Environment: LO, HI (required); ROOT (the repository's data/), the tree holding raw/ (the raw
# zone raw_zone.sh writes), stage/, log/ and tmp/; NBUCKETS (16); OUT; DUCKDB; MEM (12GB);
# SPEED_KN (100), GAP_S (600), STOP_KN (0.2) and MOVE_KN (0.5), the cleaning's speed bound, the
# silence that ends a piece and the hysteresis of the stops (20_clean.sql); STAGES
# ("1 2 3"), the stages this invocation runs, each reading what the stage before it wrote, and
# `audit` a further one, what each cleaning rule removes (26_cleaning_audit.sql) written to
# audit-stages.csv and audit-removed.csv in OUT once stages 1 and 2 have run. The SQL files and
# duckdb.sh are read from the directory holding this script.
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The query-ready zone carries a TemporalParquet footer, one document for every writer
TEMPORAL_FOOTER=$("$P/temporal_footer.sh")
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
LO=${LO:?LO is required}
HI=${HI:?HI is required}
NB=${NBUCKETS:-16}
OUT=${OUT:-$ROOT/stage/planar/${LO}_${HI}}
# The engine is $DUCKDB when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled and the session time zone at UTC.
export DUCKDB_ENGINE=${DUCKDB_ENGINE:-${DUCKDB:-}}
DUCKDB="$P/duckdb.sh"
MEM=${MEM:-12GB}
SPEED_KN=${SPEED_KN:-100}
GAP_S=${GAP_S:-600}
STOP_KN=${STOP_KN:-0.2}
MOVE_KN=${MOVE_KN:-0.5}
STAGES=${STAGES:-"1 2 3"}

want() { case " $STAGES " in *" $1 "*) return 0;; *) return 1;; esac; }

[ -x "$DUCKDB" ] || { echo "no duckdb at $DUCKDB" >&2; exit 1; }
mkdir -p "$OUT/buckets" "$OUT/clean" "$OUT/stops" "$OUT/segments" "$OUT/gen" "$ROOT/log"
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL"
trap 'rm -rf "$SPILL"' EXIT
LOG="$ROOT/log/planar-clean.tsv"

# The session settings; $1 is whether a COPY keeps the order its query states. Stage 1 and the
# cleaning write unordered data and let DuckDB reorder for speed; a segment file is written in
# start order, so a day's L0 read skips the row groups outside the day, and an L0 file in the
# chronological order a feed appended as it arrives delivers, which is what makes L0 the
# unclustered control.
settings() {
  echo "LOAD spatial;"
  echo "SET memory_limit = '$MEM';"
  echo "SET temp_directory = '$SPILL';"
  echo "SET preserve_insertion_order = ${1:-false};"
}

# Stage 1: the buckets.
if want 1; then
  {
    settings
    echo "SET VARIABLE raw_glob = '$ROOT/raw/aisdk-*.parquet';"
    echo "SET VARIABLE lo = '$LO';"
    echo "SET VARIABLE hi = '$HI';"
    echo "SET VARIABLE nbuckets = $NB;"
    echo "SET VARIABLE mid_csv = '$P/itu_mid.csv';"
    echo ".read $P/10_stage.sql"
    printf "COPY staged TO '%s' (FORMAT parquet, PARTITION_BY (bucket), OVERWRITE_OR_IGNORE);\n" \
      "$OUT/buckets"
  } > "$OUT/gen/stage.sql"
  t0=$(date +%s)
  "$DUCKDB" -unsigned -c ".read $OUT/gen/stage.sql" > "$OUT/gen/stage.log" 2>&1
  printf 'stage\t%s\t%s\t%ss\n' "$LO" "$HI" "$(( $(date +%s) - t0 ))" | tee -a "$LOG"
fi

# Stage 2: each bucket cleaned and segmented over its whole period.
if want 2; then
  for b in $(seq 0 $((NB - 1))); do
    [ -d "$OUT/buckets/bucket=$b" ] || continue
    {
      settings
      echo "SET VARIABLE bucket_glob = '$OUT/buckets/bucket=$b/*.parquet';"
      echo "SET VARIABLE speed_kn = $SPEED_KN;"
      echo "SET VARIABLE gap_s = $GAP_S;"
      echo "SET VARIABLE stop_kn = $STOP_KN;"
      echo "SET VARIABLE move_kn = $MOVE_KN;"
      echo ".read $P/20_clean.sql"
      echo ".read $P/25_segments.sql"
      printf "COPY clean TO '%s' (FORMAT parquet);\n" "$OUT/clean/bucket-$b.parquet"
      printf "COPY stop_run TO '%s' (FORMAT parquet);\n" "$OUT/stops/bucket-$b.parquet"
      echo "SET preserve_insertion_order = true;"
      printf "COPY (SELECT * FROM segment ORDER BY t0) TO '%s' (FORMAT parquet);\n" \
        "$OUT/segments/bucket-$b.parquet"
    } > "$OUT/gen/clean-$b.sql"
    t0=$(date +%s)
    "$DUCKDB" -unsigned -c ".read $OUT/gen/clean-$b.sql" > "$OUT/gen/clean-$b.log" 2>&1
    printf 'clean\t%s\t%s\tbucket %s\t%ss\n' "$LO" "$HI" "$b" "$(( $(date +%s) - t0 ))" \
      | tee -a "$LOG"
  done
fi

# Stage 3: one L0 file per calendar day of [LO, HI), checked from the written file by the shell,
# since an in-SQL guard cannot fail a DuckDB run: no moving row over SPEED_KN, no row crossing
# midnight, every row in EPSG:25832.
if want 3; then
  day="$LO"
  while [ "$day" \< "$HI" ]; do
    dest="$OUT/L0/year=${day:0:4}/month=${day:5:2}"
    mkdir -p "$dest"
    {
      settings true
      echo "SET VARIABLE out = '$OUT';"
      echo "SET VARIABLE day = '$day';"
      echo ".read $P/30_l0.sql"
      printf "COPY l0 TO '%s' (FORMAT parquet, COMPRESSION zstd, %s);\n" \
        "$dest/day-$day.parquet" "$TEMPORAL_FOOTER"
    } > "$OUT/gen/l0-$day.sql"
    t0=$(date +%s)
    "$DUCKDB" -unsigned -c ".read $OUT/gen/l0-$day.sql" > "$OUT/gen/l0-$day.log" 2>&1
    viol=$("$DUCKDB" -unsigned -noheader -list -c "
      SELECT count(*) FILTER (WHERE segment_type = 'In motion'
                                AND epoch(trip_tspan.tmax) > epoch(trip_tspan.tmin)
                                AND length(tgeompointFromEWKB(trip))
                                    / (epoch(trip_tspan.tmax) - epoch(trip_tspan.tmin))
                                    * 1.9438444924406 > $SPEED_KN)
           + count(*) FILTER (WHERE trip_tspan.tmin < dt::TIMESTAMP
                                OR trip_tspan.tmax > dt::TIMESTAMP + INTERVAL 1 DAY)
           + count(*) FILTER (WHERE srid <> 25832)
           + count(*) FILTER (WHERE trip_xmin IS DISTINCT FROM trip_bbox.xmin
                                OR trip_ymin IS DISTINCT FROM trip_bbox.ymin
                                OR trip_xmax IS DISTINCT FROM trip_bbox.xmax
                                OR trip_ymax IS DISTINCT FROM trip_bbox.ymax
                                OR trip_tmin IS DISTINCT FROM trip_tspan.tmin
                                OR trip_tmax IS DISTINCT FROM trip_tspan.tmax)
      FROM read_parquet('$dest/day-$day.parquet');" 2>/dev/null)
    if [ "${viol:-x}" != "0" ]; then
      echo "L0 INVARIANT FAILED for $day: ${viol:-no readable file}" >&2
      exit 1
    fi
    rows=$("$DUCKDB" -unsigned -noheader -list -c \
      "SELECT count(*) FROM read_parquet('$dest/day-$day.parquet');")
    printf 'L0\t%s\t%ss\t%s rows\t%s bytes\n' "$day" "$(( $(date +%s) - t0 ))" "$rows" \
      "$(stat -c %s "$dest/day-$day.parquet")" | tee -a "$LOG"
    day=$(date -d "$day + 1 day" +%F)
  done
fi

# The audit: the reports and vessels each rule keeps, and the vessels it removes.
if want audit; then
  {
    settings
    echo "SET VARIABLE raw_glob = '$ROOT/raw/aisdk-*.parquet';"
    echo "SET VARIABLE lo = '$LO';"
    echo "SET VARIABLE hi = '$HI';"
    echo "SET VARIABLE mid_csv = '$P/itu_mid.csv';"
    echo "SET VARIABLE out = '$OUT';"
    echo ".read $P/26_cleaning_audit.sql"
    printf "COPY audit_stage TO '%s' (FORMAT csv, HEADER);\n" "$OUT/audit-stages.csv"
    printf "COPY audit_removed TO '%s' (FORMAT csv, HEADER);\n" "$OUT/audit-removed.csv"
  } > "$OUT/gen/audit.sql"
  t0=$(date +%s)
  "$DUCKDB" -unsigned -c ".read $OUT/gen/audit.sql" > "$OUT/gen/audit.log" 2>&1
  printf 'audit\t%s\t%s\t%ss\n' "$LO" "$HI" "$(( $(date +%s) - t0 ))" | tee -a "$LOG"
fi
