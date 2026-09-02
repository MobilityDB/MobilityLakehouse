#!/usr/bin/env bash
# Stage 2 of the synced rebuild: L1..L4 daily + compact, projected to the trips
# schema on MinIO, then the sorted variants (L0X/L0Z/L0H, L1s..L4s).
#
# One layout at a time, projected and then deleted locally: all four daily
# layouts plus their compacts would be ~30 GB on a host with ~21 GB free, while
# a single layout's daily+compact peaks around 8 GB.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source deploy/iceberg_rest/env.rest

export MOBILITYDUCK__EXTENSION_PATH="${MOBILITYDUCK__EXTENSION_PATH:?set it in .env or export it}"
export TRIPS_DEST="${TRIPS_DEST:-s3://warehouse/trips}"

LAKEHOUSE=".venv/bin/lakehouse"
PYTHON=".venv/bin/python"
MONTH="${MONTH:-2026-01}"
DAILY_DIR="data/layouts_daily"
COMPACT_DIR="data/layout_compact"
LOG="logs/rebuild_layouts.log"
mkdir -p logs

NUM_SHARDS=16
L2_REGION_M=50000
L2_TILE_M=1000
L3_REGION_M=50000
L3_SEGS_PER_BOX=16
L4_TIME_BIN="1 hour"

say() { echo "$*" | tee -a "$LOG"; }
die() { say "FAILED: $*"; exit 1; }

say "=== stage 2: layouts -> $TRIPS_DEST  (month $MONTH) ==="

say "[L0] project"
"$PYTHON" -m lakehouse.process.trips L0 >>"$LOG" 2>&1 || die "project L0"
say "[L0] projected"

for L in L1 L2 L3 L4; do
    case "$L" in
      L1) ARGS=(--num-shards "$NUM_SHARDS") ;;
      L2) ARGS=(--region-size-m "$L2_REGION_M" --tile-size-m "$L2_TILE_M") ;;
      L3) ARGS=(--region-size-m "$L3_REGION_M" --segs-per-box "$L3_SEGS_PER_BOX") ;;
      L4) ARGS=(--time-bin "$L4_TIME_BIN") ;;
    esac

    say "[$L] build daily"
    "$LAKEHOUSE" pipeline build-layout "$L" --month "$MONTH" --granularity daily \
        --layout-dir "$DAILY_DIR" "${ARGS[@]}" >>"$LOG" 2>&1 || die "build $L"

    say "[$L] compact"
    "$LAKEHOUSE" pipeline compact-layout "$L" --month "$MONTH" \
        --src-dir "$DAILY_DIR" --dst-dir "$COMPACT_DIR" >>"$LOG" 2>&1 || die "compact $L"

    say "[$L] project daily + compact"
    "$PYTHON" -m lakehouse.process.trips "${L}_daily" >>"$LOG" 2>&1 || die "project ${L}_daily"
    "$PYTHON" scripts/project_compact.py "$L"     >>"$LOG" 2>&1 || die "project compact $L"

    rm -rf "${DAILY_DIR:?}/$L" "${COMPACT_DIR:?}/$L"
    say "[$L] DONE  (free $(df -g . | tail -1 | awk '{print $4}')Gi)"
done

say "[sorted] L0X / L0Z / L0H"
"$PYTHON" -m lakehouse.process.sort_daily >>"$LOG" 2>&1 || die "build_daily_sorted"
say "[sorted] L1s..L4s"
"$PYTHON" -m lakehouse.process.sort_compact --layouts L1,L2,L3,L4 >>"$LOG" 2>&1 \
    || die "build_sorted_compact"

say "=== stage 2 complete ==="
