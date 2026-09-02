#!/usr/bin/env bash
set -euo pipefail

YEAR="${YEAR:-}"
if [[ -n "$YEAR" ]]; then
    MONTHS="$(for m in $(seq 1 12); do printf '%s-%02d ' "$YEAR" "$m"; done)"
fi
MONTHS="${MONTHS:-2026-01}"

L3_REGION_M="${L3_REGION_M:-50000}"
L3_SEGS_PER_BOX="${L3_SEGS_PER_BOX:-16}"
DAILY_DIR="${DAILY_DIR:-data/layouts_daily}"
COMPACT_DIR="${COMPACT_DIR:-data/layout_compact}"

PRUNE_RAW="${PRUNE_RAW:-1}"
PRUNE_DAILY="${PRUNE_DAILY:-0}"

BENCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BENCH_ROOT"

LAKEHOUSE=".venv/bin/lakehouse"; [ -x "$LAKEHOUSE" ] || LAKEHOUSE="lakehouse"
PYTHON=".venv/bin/python";      [ -x "$PYTHON" ]     || PYTHON="python"

if [[ "${TRIPS_DEST:-}" == s3://* ]]; then
    source deploy/deploy/iceberg_rest/env.rest
    echo "trips dest = $TRIPS_DEST  (MinIO + REST must be up)"
fi

stage() { echo; echo "==================== $* ===================="; echo; }

preflight() {
    command -v "${LAKEHOUSE%% *}" >/dev/null 2>&1 || [ -x "$LAKEHOUSE" ] || {
        echo "no 'lakehouse' command. Create the venv and install:"
        echo "  python -m venv .venv && . .venv/bin/activate"
        echo "  pip install -e pipeline"; exit 1; }

    local ext="${MOBILITYDUCK_EXT:-${MOBILITYDUCK__EXTENSION_PATH:-}}"
    if [ -z "$ext" ] && [ -f .env ]; then
        ext="$(sed -n 's/^MOBILITYDUCK__EXTENSION_PATH=//p' .env | tail -1)"
    fi
    [ -n "$ext" ] || { echo "MOBILITYDUCK_EXT is not set (and .env does not set"
        echo "MOBILITYDUCK__EXTENSION_PATH). Export it and rerun."; exit 1; }
    [ -f "$ext" ] || { echo "MobilityDuck extension not found at:"; echo "  $ext"
        echo "Fix the path and rerun."; exit 1; }

    local n_months free_gb need_gb
    n_months=$(echo $MONTHS | wc -w | tr -d ' ')
    free_gb=$(df -g . | awk 'NR==2{print $4}')
    need_gb=$(( 11 + 11 * n_months ))
    echo "months     : $n_months  ($MONTHS)"
    if [ -x .venv/bin/lakehouse ]; then
        echo "lakehouse  : $PWD/.venv/bin/lakehouse"
    else
        echo "lakehouse  : $(command -v lakehouse)   <- not ./.venv; is that intended?"
    fi
    echo "extension  : $ext"
    echo "prune      : raw=$PRUNE_RAW daily=$PRUNE_DAILY"
    echo "disk       : ${free_gb} GB free, needs roughly ${need_gb} GB"
    if [ "$free_gb" -lt "$need_gb" ]; then
        echo
        echo "WARNING: that is not enough. Either free space, run fewer months,"
        echo "or set PRUNE_RAW=1 PRUNE_DAILY=1 if they are off."
        [ "${FORCE:-0}" = "1" ] || { echo "Set FORCE=1 to run anyway."; exit 1; }
    fi
}

stage "preflight"
preflight

last_day() { date -j -f "%Y-%m-%d" "$1-01" "+%Y-%m-%d" >/dev/null 2>&1 \
    && date -j -v+1m -v-1d -f "%Y-%m-%d" "$1-01" "+%Y-%m-%d" \
    || date -d "$1-01 +1 month -1 day" "+%Y-%m-%d"; }

for MONTH in $MONTHS; do
    FIRST="$MONTH-01"
    LAST="$(last_day "$MONTH")"

    stage "$MONTH  ingest raw  ($FIRST .. $LAST)"
    "$LAKEHOUSE" pipeline ingest-range "$FIRST" "$LAST"

    stage "$MONTH  build L0 base segments"
    "$LAKEHOUSE" pipeline build-l0 --start-day "$FIRST" --end-day "$LAST"

    [[ "$PRUNE_RAW" == "1" ]] && rm -f data/raw/aisdk-"$MONTH"-*.parquet

    stage "$MONTH  build L3 (daily)"
    "$LAKEHOUSE" pipeline build-layout L3 --month "$MONTH" --granularity daily \
        --layout-dir "$DAILY_DIR" \
        --region-size-m "$L3_REGION_M" --segs-per-box "$L3_SEGS_PER_BOX"

    stage "$MONTH  compact L3 -> monthly"
    "$LAKEHOUSE" pipeline compact-layout L3 --month "$MONTH" \
        --src-dir "$DAILY_DIR" --dst-dir "$COMPACT_DIR"

    [[ "$PRUNE_DAILY" == "1" ]] && rm -rf "$DAILY_DIR/L3/year=${MONTH%-*}/month=${MONTH#*-}"
done

stage "project -> trips schema"
"$PYTHON" -m lakehouse.process.trips L3 L3c

stage "sort the compact layout -> L3s"
"$PYTHON" -m lakehouse.process.sort_compact --layouts L3

stage "done"
echo "L3   (daily)            ${TRIPS_DEST:-data/trips}/layouts_daily/L3"
echo "L3c  (compact)          ${TRIPS_DEST:-data/trips}/layout_compact/L3"
echo "L3s  (compact + sorted) ${TRIPS_DEST:-data/trips}/layout_compact/L3s   <- the query input"
