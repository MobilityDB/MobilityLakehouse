#!/usr/bin/env bash
# Prefetch DMA day zips alongside the serial stage-1 loop.
#
# Ingest is ~95% download at ~3.6 MB/s on one stream, and the pipe saturates
# around 4-5 MB/s, so a second stream running ahead of the loop recovers roughly
# 20-30% -- not more. The loop's patched ingest.py reuses any zip already sitting
# in data/raw, so a prefetched day costs it nothing.
#
# Two things this must not get wrong:
#   * ingest.py accepts any zip with size > 0 as complete, so a half-written file
#     would be extracted as-is. Download to .part and rename only when curl exits
#     clean, so the loop never observes a partial zip.
#   * Each zip is ~550 MB. Keep at most WINDOW unconsumed ones (the loop deletes
#     each after use) so this cannot fill the disk.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
RAW="data/raw"
WINDOW="${WINDOW:-3}"
MIN_FREE_GB="${MIN_FREE_GB:-8}"
END="${END:-2026-01-31}"
mkdir -p "$RAW"

d="${START:-2026-01-16}"
while [[ "$d" < "$END" || "$d" == "$END" ]]; do
    l0="data/L0/L0/year=${d:0:4}/month=${d:5:2}/day=$d.parquet"
    zip="$RAW/aisdk-$d.zip"

    if [[ -f "$l0" || -f "$zip" ]]; then
        d=$(date -j -v+1d -f %Y-%m-%d "$d" +%Y-%m-%d); continue
    fi

    # throttle: wait while the loop is behind us or the disk is tight
    while true; do
        pending=$(ls "$RAW"/aisdk-*.zip 2>/dev/null | wc -l | tr -d ' ')
        free=$(df -g . | tail -1 | awk '{print $4}')
        if (( pending < WINDOW && free > MIN_FREE_GB )); then break; fi
        pgrep -f rebuild_ingest_l0.sh >/dev/null || { echo "[prefetch] loop gone; stop"; exit 0; }
        sleep 30
    done

    echo "[prefetch] $d ..."
    if curl -fsS --max-time 1800 -o "$zip.part" "http://aisdata.ais.dk/aisdk-$d.zip"; then
        mv "$zip.part" "$zip"          # atomic: loop only ever sees a complete zip
        echo "[prefetch] $d ready"
    else
        rm -f "$zip.part"
        echo "[prefetch] $d FAILED (loop will fetch it itself)"
    fi
    d=$(date -j -v+1d -f %Y-%m-%d "$d" +%Y-%m-%d)
done
echo "[prefetch] done"
