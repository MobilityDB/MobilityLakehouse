#!/usr/bin/env bash
# =============================================================================
# Tile-size sensitivity sweep: rebuild L2/L3 at 25 km and 100 km region cells
# and benchmark them against the existing 50 km layouts.
#
# Run on the machine with the MobilityDuck extension (the same setup as the
# main benchmark), from benchmark/, with the MinIO + Iceberg REST stack up:
#
#     bash scripts/tile_sweep.sh
#
# The sweep layouts live in lakehouse.query.registry behind LAKEHOUSE_SWEEP=1, so the
# main benchmark and its results are untouched.
#
# Prerequisite: the raw L0 base (data/L0) must exist. If it was cleaned up
# after the main build, rebuild it first:
#     lakehouse pipeline build-l0 --start-day 2026-01-01 --end-day 2026-01-31
#
# Companion: scripts/tile_size_model.py computes the file-level analysis from
# the L0 bounds alone (no MEOS needed); this script is the full validation.
# =============================================================================
set -euo pipefail

MONTH="2026-01"
export LAKEHOUSE_SWEEP=1

# --- 1. Build daily L2/L3 at each sweep size --------------------------------
for SIZE_KM in 25 100; do
  SIZE_M=$((SIZE_KM * 1000))
  DAILY_DIR="data/sweep_daily_${SIZE_KM}km"
  COMPACT_DIR="data/sweep_compact_${SIZE_KM}km"
  for LAYOUT in L2 L3; do
    echo "=== build ${LAYOUT} @ ${SIZE_KM} km (daily) ==="
    lakehouse pipeline build-layout "${LAYOUT}" \
      --month "${MONTH}" \
      --granularity daily \
      --region-size-m "${SIZE_M}" \
      --layout-dir "${DAILY_DIR}"

    echo "=== compact ${LAYOUT} @ ${SIZE_KM} km ==="
    lakehouse pipeline compact-layout "${LAYOUT}" \
      --month "${MONTH}" \
      --src-dir "${DAILY_DIR}" \
      --dst-dir "${COMPACT_DIR}"
  done
done

# --- 2. Project the sweep layouts onto the trips schema ---------------------
# (build_trips / iceberg_to_s3 filter by layout KEY: <name>_<granularity>)
SWEEP_KEYS="L2d25_daily L3d25_daily L2d100_daily L3d100_daily \
L2c25_compact L3c25_compact L2c100_compact L3c100_compact"

echo "=== trips projection (sweep layouts) ==="
python -m lakehouse.process.trips ${SWEEP_KEYS}

# --- 3. Register the sweep tables in the Iceberg catalog --------------------
echo "=== Iceberg registration (sweep layouts) ==="
python -m lakehouse.store.publish ${SWEEP_KEYS}

# --- 4. Benchmark: sweep layouts + the 50 km reference ----------------------
echo "=== benchmark ==="
python -m lakehouse.query.run_iceberg --iters 5 --sels hour,day,week \
  --layouts L0,L2,L3,L2s,L3s,L2d25,L3d25,L2d100,L3d100,L2c25,L3c25,L2c100,L3c100 \
  --out results/tile_sweep.csv

echo "=== done: results/tile_sweep.csv ==="
echo "Compare bytes-read %, runtime, and answers against the 50 km rows;"
echo "recall follows from the answer columns against L0."
