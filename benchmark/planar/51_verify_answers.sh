#!/usr/bin/env bash
# THE GATE ON THE PRUNING FIGURES: every layout answers each window with the same set of vessels.
#
# A layout changes only WHERE a segment is stored, never what the corpus says, so for a window
# every layout owes L0's answer; until that holds, a pruning figure is unreadable, since a layout
# that reads little because it LOST rows would report the best number. THE ANSWER IS TAKEN THROUGH
# `atStbox`, not the stored box: the box admits a row whose bounding box meets the window, a
# superset, while the vessels whose trajectory meets the window in space and time together are
# what a user asks for and the only layout-invariant quantity. The stored-box predicate stays
# written out in front of it, because it names only the covering columns and so lets the reader
# skip row groups from the footer. L0, the uncut corpus, is the truth.
#
#   RUN=<run> ./51_verify_answers.sh
#
# Environment: RUN (required); ROOT (the repository's data/); DUCKDB_ENGINE; LAYOUTS (all twelve);
# VERIFY_OUT (the table appended to, $ROOT/results/planar/layout-answers.csv); VERIFY_MEMORY
# (10GB); VERIFY_THREADS (4).
set -euo pipefail

ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
# The engine is DUCKDB_ENGINE when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled and the session time zone at UTC.
DUCKDB="$(dirname "${BASH_SOURCE[0]}")/duckdb.sh"
WINDOWS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/windows_25832.csv"
LAYOUTS=${LAYOUTS:-"L0 L0X L0Z L0H L1 L2 L3 L4 L1s L2s L3s L4s"}
OUT=${VERIFY_OUT:-$ROOT/results/planar/layout-answers.csv}
MEM=${VERIFY_MEMORY:-10GB}
THREADS=${VERIFY_THREADS:-4}
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL" "$(dirname "$OUT")" "$ROOT/log"
trap 'rm -rf "$SPILL"' EXIT

layout_glob() {
  case "$1" in
    L0)          echo "$RUN/L0/year=*/month=*/day-*.parquet" ;;
    L0X|L0Z|L0H) echo "$RUN/layouts_daily/$1/day-*.parquet" ;;
    L1|L2|L3|L4) echo "$RUN/layouts_daily/$1/day-*/**/*.parquet" ;;
    L1s|L2s|L3s|L4s) echo "$RUN/layout_compact/$1/**/*.parquet" ;;
    *) echo "unknown layout $1" >&2; return 1 ;;
  esac
}

[ -s "$OUT" ] || echo "layout,window,vessels" > "$OUT"

while IFS=, read -r name qx0 qy0 qx1 qy1 qt0 qt1; do
  # The window's instants are UTC and the box states it, so the exact predicate reads the instants
  # the stored time-span filter beside it reads, whatever zone a zone-less literal is read in.
  box="SRID=25832;STBOX XT((($qx0,$qy0),($qx1,$qy1)),[${qt0}+00,${qt1}+00])"
  for L in $LAYOUTS; do
    glob=$(layout_glob "$L")
    "$DUCKDB" -unsigned -noheader -list -c "
SET memory_limit = '$MEM'; SET threads = $THREADS; SET temp_directory = '$SPILL';
SET preserve_insertion_order = false;
SELECT '$L' || ',' || '$name' || ',' || count(DISTINCT MMSI)
FROM read_parquet('$glob', hive_partitioning = false)
WHERE trip_bbox.xmax >= $qx0 AND trip_bbox.xmin <= $qx1
  AND trip_bbox.ymax >= $qy0 AND trip_bbox.ymin <= $qy1
  AND trip_tspan.tmax >= TIMESTAMP '$qt0' AND trip_tspan.tmin <= TIMESTAMP '$qt1'
  AND atStbox(tgeompointFromEWKB(trip), stbox('$box')) IS NOT NULL;" \
      2>>"$ROOT/log/planar-verify-errors.log" | tee -a "$OUT"
  done
done < "$WINDOWS"

"$DUCKDB" -unsigned -noheader -list -c "
WITH a AS (SELECT * FROM read_csv('$OUT', header = true,
             columns = {'layout': 'VARCHAR', 'window': 'VARCHAR', 'vessels': 'BIGINT'})),
     truth AS (SELECT \"window\", vessels AS truth FROM a WHERE layout = 'L0')
SELECT CASE WHEN count(*) FILTER (WHERE a.vessels IS DISTINCT FROM t.truth) = 0
            THEN 'ALL LAYOUTS AGREE WITH L0 over ' || count(*) || ' (layout,window) pairs'
            ELSE 'MISMATCH in ' || count(*) FILTER (WHERE a.vessels IS DISTINCT FROM t.truth)
                 || ' of ' || count(*) || ' pairs' END
FROM a JOIN truth t USING (\"window\");"
