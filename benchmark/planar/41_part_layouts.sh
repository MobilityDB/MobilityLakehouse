#!/usr/bin/env bash
# The partitioning layouts L1, L2, L3 and L4 of the planar corpus, from its daily L0 files. Each
# decides which FILE a segment goes to; every row keeps the columns of the trips schema, with the
# covering columns of the TemporalParquet specification computed from the row's own trajectory,
# and the partition value lives in the object key (DuckDB's PARTITION_BY writes Hive directories).
#
#   L1  hash(MMSI) % 16, trajectories never cut: the non-spatial control
#   L2  regular tiles, spaceSplit on a fixed grid, one file per tile
#   L3  adaptive tiles, splitEachNStboxes + atStbox, one file per region cell
#   L4  time bins, timeSplit on a fixed interval, one file per bin
#
# A stationary segment is never split: a vessel at rest has almost no extent to divide and is
# assigned whole. A split keeps every piece it produces, since a piece of one or two instants is
# where a trajectory clips a tile and dropping it drops the vessel from that tile; the one check a
# piece passes is that its time span lies within its parent's. Each build reports, per layer, the
# pieces that check discards out of the pieces the split produces (the DISCARD lines of the day's
# log, and the PART line of log/planar-part.tsv); SPAN_COUNT=1 runs the splits and that count
# alone, writing no layout file, for a run whose layouts already exist (log/planar-span.tsv).
#
# A day's statements run under DuckDB's timer, each layer's after a marker naming it, so the PART
# and SPAN lines also carry each layer's own seconds, the loading of the day the layers share
# counted as setup.
#
#   RUN=data/stage/planar/2026-01-01_2026-02-01 benchmark/planar/41_part_layouts.sh [day ...]
#
# Environment: RUN (required); ROOT (the repository's data/), the tree holding log/ and tmp/;
# DUCKDB (a MobilityDuck whose timeSplit takes a column); LAYERS ("L1 L2 L3 L4", "L2 L3 L4" under
# SPAN_COUNT); LAYOUT_MEMORY (12GB); LAYOUT_THREADS (4); CELL_SIZE (50000); ADAPTIVE_NSEG (64);
# TIME_BIN_SECONDS (3600); SPAN_COUNT (unset).
set -euo pipefail

ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
L0="$RUN/L0"
OUT="$RUN/layouts_daily"
# The engine is $DUCKDB when set, else the one engine.path names; every call goes through
# duckdb.sh, which starts it with the arrow lambda syntax disabled.
export DUCKDB_ENGINE=${DUCKDB_ENGINE:-${DUCKDB:-}}
DUCKDB="$(dirname "${BASH_SOURCE[0]}")/duckdb.sh"
SPAN_COUNT=${SPAN_COUNT:-}
if [ -n "$SPAN_COUNT" ]; then LAYERS=${LAYERS:-"L2 L3 L4"}; else LAYERS=${LAYERS:-"L1 L2 L3 L4"}; fi
MEM=${LAYOUT_MEMORY:-12GB}
THREADS=${LAYOUT_THREADS:-4}
CELL=${CELL_SIZE:-50000.0}
NSEG=${ADAPTIVE_NSEG:-64}
BINSEC=${TIME_BIN_SECONDS:-3600}
SPILL="$ROOT/tmp/spill.$$.$(date +%s%N)"
mkdir -p "$SPILL" "$RUN/gen" "$ROOT/log"
trap 'rm -rf "$SPILL"' EXIT

# L0 holds one file per UTC day under the Hive year=/month= directories run_clean.sh writes.
ls "$L0"/year=*/month=*/day-*.parquet >/dev/null 2>&1 || { echo "no L0 day files under $L0" >&2; exit 1; }
for L in $LAYERS; do mkdir -p "$OUT/$L"; done

want() { case " $LAYERS " in *" $1 "*) return 0;; *) return 1;; esac; }

# The trips columns of a piece, from its trajectory `p` and box `b`.
COLS="MMSI, ship_type, segment_type, asEWKB(p) AS trip,
  {'xmin': Xmin(b), 'ymin': Ymin(b), 'xmax': Xmax(b), 'ymax': Ymax(b)} AS trip_bbox,
  {'tmin': Tmin(b)::TIMESTAMP, 'tmax': Tmax(b)::TIMESTAMP} AS trip_tspan,
  Xmin(b) AS trip_xmin, Ymin(b) AS trip_ymin, Xmax(b) AS trip_xmax, Ymax(b) AS trip_ymax,
  Tmin(b)::TIMESTAMP AS trip_tmin, Tmax(b)::TIMESTAMP AS trip_tmax,
  SRID(p) AS srid, dt"
# The same columns for a stationary segment, which L0 already carries.
SCOLS="MMSI, ship_type, segment_type, trip, trip_bbox, trip_tspan,
  trip_xmin, trip_ymin, trip_xmax, trip_ymax, trip_tmin, trip_tmax, srid, dt"
# The region cell of a box centre.
CELLX="(floor(((trip_bbox.xmin + trip_bbox.xmax) / 2) / $CELL))::INTEGER"
CELLY="(floor(((trip_bbox.ymin + trip_bbox.ymax) / 2) / $CELL))::INTEGER"
# The one check a split piece passes: its time span lies within its parent's.
KEEP="Tmin(b)::TIMESTAMP >= ptmin AND Tmax(b)::TIMESTAMP <= ptmax"

# The pieces of layer $1 (table $2) the check discards, out of the pieces the split produced.
discard() {
  echo "SELECT 'DISCARD $1 ' || count(*) FILTER (WHERE NOT ($KEEP)) || ' of ' || count(*) AS discard FROM $2;"
}
# The marker the statements of layer $1 follow in the day's log.
marker() { echo "SELECT 'LAYER $1' AS layer;"; }
# A statement that writes a layout, passed through on a build and dropped under SPAN_COUNT.
build_only() { if [ -z "$SPAN_COUNT" ]; then cat; else cat > /dev/null; fi; }

# Each layer's seconds in the day's log $1: the timer's real time of every statement after the
# layer's marker, the statements before the first marker counted as setup.
layer_seconds() {
  awk -v cur=setup '
    /LAYER L[0-9]/ { match($0, /LAYER L[0-9]/); cur = substr($0, RSTART + 6, 2) }
    /^Run Time \(s\): real / { t[cur] += $5 }
    END {
      n = split("setup L1 L2 L3 L4", k, " ")
      for (i = 1; i <= n; i++)
        if (k[i] in t) printf "%s%s=%.1f", (s++ ? " " : ""), k[i], t[k[i]]
    }' "$1"
}

build_one() {
  local day="$1"
  local src
  src=$(ls "$L0"/year=*/month=*/day-"$day".parquet 2>/dev/null) || { echo "no L0 for $day" >&2; return 1; }
  local tag=part
  [ -z "$SPAN_COUNT" ] || tag=span
  local script="$RUN/gen/$tag-$day.sql"
  # A day's partition directories are emptied first: OVERWRITE_OR_IGNORE replaces the files a
  # write produces and keeps any other file already there, and a partition written as two files
  # by one build and as one by the next would keep the older second file, duplicating its rows.
  if [ -z "$SPAN_COUNT" ]; then
    for L in $LAYERS; do rm -rf "$OUT/$L/day-$day"; mkdir -p "$OUT/$L/day-$day"; done
  fi
  {
    cat <<HDR
.timer on
LOAD spatial;
SET memory_limit = '$MEM';
SET threads = $THREADS;
SET temp_directory = '$SPILL';
SET preserve_insertion_order = false;
CREATE OR REPLACE TEMP TABLE src AS SELECT * FROM read_parquet('$src', hive_partitioning = false);
CREATE OR REPLACE TEMP TABLE moving AS
  SELECT MMSI, ship_type, segment_type, dt, trip_tspan.tmin AS ptmin, trip_tspan.tmax AS ptmax,
         tgeompointFromEWKB(trip) AS trip
  FROM src WHERE segment_type = 'In motion';
CREATE OR REPLACE TEMP TABLE stationary AS SELECT * FROM src WHERE segment_type = 'Stationary';
HDR

    # Each table is dropped once its last reader has run, so a layer is built beside the day's
    # moving and stationary rows only, never beside the pieces of the layers before it.
    if want L1; then
      marker L1
      build_only <<HDR
COPY (SELECT *, (hash(MMSI) % 16)::INTEGER AS shard FROM src)
  TO '$OUT/L1/day-$day' (FORMAT parquet, PARTITION_BY (shard), COMPRESSION zstd, OVERWRITE_OR_IGNORE);
HDR
    fi
    echo "DROP TABLE src;"

    if want L2; then
      marker L2
      cat <<HDR
CREATE OR REPLACE TEMP TABLE l2 AS
SELECT m.MMSI, m.ship_type, m.segment_type, m.dt, m.ptmin, m.ptmax, s.tpoint AS p,
  stbox(s.tpoint) AS b,
  (floor(ST_X(s.spaceBin) / $CELL))::INTEGER AS cell_x,
  (floor(ST_Y(s.spaceBin) / $CELL))::INTEGER AS cell_y
FROM moving m, spaceSplit(m.trip, $CELL, $CELL, $CELL) s
WHERE s.tpoint IS NOT NULL;
HDR
      discard L2 l2
      build_only <<HDR
COPY (
  SELECT $COLS, cell_x, cell_y FROM l2
  WHERE $KEEP
  UNION ALL
  SELECT $SCOLS, $CELLX, $CELLY FROM stationary)
  TO '$OUT/L2/day-$day' (FORMAT parquet, PARTITION_BY (cell_x, cell_y), COMPRESSION zstd, OVERWRITE_OR_IGNORE);
HDR
      echo "DROP TABLE l2;"
    fi

    # A trajectory's pieces are built inside one call over its list of boxes: a lateral join
    # over the boxes carries the whole trajectory on every box row, which holds the trajectory
    # once per box, quadratic in its length (12 GiB on a 130 MiB day).
    if want L3; then
      marker L3
      cat <<HDR
CREATE OR REPLACE TEMP TABLE l3 AS
WITH pieces AS (
  SELECT MMSI, ship_type, segment_type, dt, ptmin, ptmax,
    unnest(list_transform(splitEachNStboxes(trip, $NSEG), lambda bx: atStbox(trip, bx))) AS p
  FROM moving)
SELECT *, stbox(p) AS b FROM pieces WHERE p IS NOT NULL;
HDR
      discard L3 l3
      build_only <<HDR
COPY (
  SELECT $COLS, (floor(((Xmin(b) + Xmax(b)) / 2) / $CELL))::INTEGER AS cell_x,
    (floor(((Ymin(b) + Ymax(b)) / 2) / $CELL))::INTEGER AS cell_y
  FROM l3 WHERE $KEEP
  UNION ALL
  SELECT $SCOLS, $CELLX, $CELLY FROM stationary)
  TO '$OUT/L3/day-$day' (FORMAT parquet, PARTITION_BY (cell_x, cell_y), COMPRESSION zstd, OVERWRITE_OR_IGNORE);
HDR
      echo "DROP TABLE l3;"
    fi

    if want L4; then
      marker L4
      cat <<HDR
CREATE OR REPLACE TEMP TABLE l4 AS
SELECT m.MMSI, m.ship_type, m.segment_type, m.dt, m.ptmin, m.ptmax, s.tgeompoint AS p,
  stbox(s.tgeompoint) AS b, (floor(epoch(s.time) / $BINSEC))::BIGINT AS time_bin
FROM moving m, timeSplit(m.trip, to_seconds($BINSEC)) s
WHERE s.tgeompoint IS NOT NULL;
HDR
      discard L4 l4
      build_only <<HDR
COPY (
  SELECT $COLS, time_bin FROM l4
  WHERE $KEEP
  UNION ALL
  SELECT $SCOLS, (floor(epoch(trip_tspan.tmin) / $BINSEC))::BIGINT FROM stationary)
  TO '$OUT/L4/day-$day' (FORMAT parquet, PARTITION_BY (time_bin), COMPRESSION zstd, OVERWRITE_OR_IGNORE);
HDR
      echo "DROP TABLE l4;"
    fi
  } > "$script"

  local t0 counts="" discards seconds
  t0=$(date +%s)
  "$DUCKDB" -unsigned -c ".read $script" > "$RUN/gen/$tag-$day.log" 2>&1
  discards=$({ grep -o 'DISCARD L[0-9] [0-9]* of [0-9]*' "$RUN/gen/$tag-$day.log" || true; } \
    | cut -d' ' -f2- | paste -sd' ' -)
  seconds=$(layer_seconds "$RUN/gen/$tag-$day.log")
  if [ -n "$SPAN_COUNT" ]; then
    printf 'SPAN\t%s\t%ss\tdiscarded %s\tseconds %s\n' "$day" "$(( $(date +%s) - t0 ))" \
      "$discards" "$seconds" | tee -a "$ROOT/log/planar-span.tsv"
    return
  fi
  for L in $LAYERS; do
    counts="$counts $L=$(find "$OUT/$L/day-$day" -name '*.parquet' 2>/dev/null | wc -l)"
  done
  printf 'PART\t%s\t%ss\t%s files\tdiscarded %s\tseconds %s\n' "$day" "$(( $(date +%s) - t0 ))" \
    "$counts" "$discards" "$seconds" | tee -a "$ROOT/log/planar-part.tsv"
}

if [ "$#" -gt 0 ]; then
  for day in "$@"; do build_one "$day"; done
else
  for f in "$L0"/year=*/month=*/day-*.parquet; do
    day=$(basename "$f" .parquet); day=${day#day-}
    build_one "$day"
  done
fi
