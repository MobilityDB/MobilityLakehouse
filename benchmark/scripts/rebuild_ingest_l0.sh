#!/usr/bin/env bash
# Stage 1 of the synced rebuild: re-ingest 2026-01 from DMA and rebuild L0 with
# the UTC-fixed pipeline, one day at a time.
#
# Day-by-day with the raw parquet deleted right after its L0 is written: the
# whole month of raw is ~12 GB and the host has ~25 GB free, so keeping only the
# current day bounds the footprint at ~3 GB transient instead.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

export MOBILITYDUCK__EXTENSION_PATH="${MOBILITYDUCK__EXTENSION_PATH:?set it in .env or export it}"

LAKEHOUSE=".venv/bin/lakehouse"
START="${START:-2026-01-01}"
END="${END:-2026-01-31}"
LOG="logs/rebuild_ingest_l0.log"
mkdir -p logs

echo "=== stage 1: ingest + L0, $START .. $END ===" | tee "$LOG"

d="$START"
while [[ "$d" < "$END" || "$d" == "$END" ]]; do
    l0="data/L0/L0/year=${d:0:4}/month=${d:5:2}/day=$d.parquet"
    if [[ -f "$l0" ]]; then
        echo "[$d] L0 already present; skipped" | tee -a "$LOG"
    else
        echo "[$d] ingest..." | tee -a "$LOG"
        if ! "$LAKEHOUSE" pipeline ingest-day "$d" >>"$LOG" 2>&1; then
            echo "[$d] INGEST FAILED (see $LOG)" | tee -a "$LOG"
            d=$(date -j -v+1d -f %Y-%m-%d "$d" +%Y-%m-%d); continue
        fi
        echo "[$d] build-l0..." | tee -a "$LOG"
        if ! "$LAKEHOUSE" pipeline build-l0 --day "$d" >>"$LOG" 2>&1; then
            echo "[$d] BUILD-L0 FAILED (see $LOG)" | tee -a "$LOG"
            d=$(date -j -v+1d -f %Y-%m-%d "$d" +%Y-%m-%d); continue
        fi
    fi
    rm -f "data/raw/aisdk-$d.parquet" "data/raw/aisdk-$d.csv" "data/raw/aisdk-$d.zip"
    free=$(df -g . | tail -1 | awk '{print $4}')
    echo "[$d] DONE  (free ${free}Gi)" | tee -a "$LOG"
    d=$(date -j -v+1d -f %Y-%m-%d "$d" +%Y-%m-%d)
done

n=$(find data/L0 -name '*.parquet' | wc -l | tr -d ' ')
echo "=== stage 1 complete: $n L0 day file(s) ===" | tee -a "$LOG"
