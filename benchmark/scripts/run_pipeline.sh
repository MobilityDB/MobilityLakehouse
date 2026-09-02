#!/usr/bin/env bash
set -euo pipefail

START="${START:-2026-01-01}"
END="${END:-2026-01-31}"
MONTH="${MONTH:-2026-01}"

export TRIPS_DEST="${TRIPS_DEST:-s3://warehouse/trips}"

DAILY_DIR="data/layouts_daily"
COMPACT_DIR="data/layout_compact"

NUM_SHARDS=16
L2_REGION_M=50000
L2_TILE_M=1000
L3_REGION_M=50000
L3_SEGS_PER_BOX=16
L4_TIME_BIN="1 hour"

BENCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BENCH_ROOT"

LAKEHOUSE=".venv/bin/lakehouse"
PYTHON=".venv/bin/python"
[ -x "$LAKEHOUSE" ] || LAKEHOUSE="lakehouse"
[ -x "$PYTHON" ]    || PYTHON="python"

stage() { echo; echo "==================== $* ===================="; echo; }

if [[ "$TRIPS_DEST" == s3://* ]]; then
    source deploy/deploy/iceberg_rest/env.rest
    echo "trips dest = $TRIPS_DEST  (writing to object storage; MinIO+REST must be up)"
fi

stage "ingest raw  ($START .. $END)"
"$LAKEHOUSE" pipeline ingest-range "$START" "$END"

stage "build L0 base segments"
"$LAKEHOUSE" pipeline build-l0

stage "build daily layouts L1..L4 ($MONTH)"
"$LAKEHOUSE" pipeline build-layout L1 --month "$MONTH" --granularity daily \
    --layout-dir "$DAILY_DIR" --num-shards "$NUM_SHARDS"
"$LAKEHOUSE" pipeline build-layout L2 --month "$MONTH" --granularity daily \
    --layout-dir "$DAILY_DIR" --region-size-m "$L2_REGION_M" --tile-size-m "$L2_TILE_M"
"$LAKEHOUSE" pipeline build-layout L3 --month "$MONTH" --granularity daily \
    --layout-dir "$DAILY_DIR" --region-size-m "$L3_REGION_M" --segs-per-box "$L3_SEGS_PER_BOX"
"$LAKEHOUSE" pipeline build-layout L4 --month "$MONTH" --granularity daily \
    --layout-dir "$DAILY_DIR" --time-bin "$L4_TIME_BIN"

stage "compact daily -> compact layouts L1..L4 ($MONTH)"
for L in L1 L2 L3 L4; do
    "$LAKEHOUSE" pipeline compact-layout "$L" --month "$MONTH" \
        --src-dir "$DAILY_DIR" --dst-dir "$COMPACT_DIR"
done

stage "project layouts -> trips schema ($TRIPS_DEST)"
"$PYTHON" -m lakehouse.process.trips

stage "build L0 in-file-sort layouts (L0X, L0Z, L0H)"
"$PYTHON" -m lakehouse.process.sort_daily

stage "build sorted-compact layouts (L1s..L4s)"
"$PYTHON" -m lakehouse.process.sort_compact --layouts L1,L2,L3,L4

stage "done, trips layers built at $TRIPS_DEST"
echo "raw     : data/raw/aisdk-*.parquet            (local)"
echo "sources : data/L0 , $DAILY_DIR , $COMPACT_DIR  (local, build inputs)"
echo "trips   : $TRIPS_DEST/<layout>/   (11-col schema: the query input, Parquet in MinIO)"
