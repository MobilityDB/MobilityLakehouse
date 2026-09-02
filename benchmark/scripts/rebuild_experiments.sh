#!/usr/bin/env bash
# Stage 3 of the synced rebuild: re-register the Iceberg tables over the rebuilt
# trips data, re-run the benchmark for the focus layouts, and diff against the
# preserved pre-sync results.
#
# --iters 5 matches the old run (results/*.csv carry n_iters=5); a different
# iteration count would make the runtime columns incomparable.
# Output goes to *_synced.csv so results/iceberg.csv and results/parquet.csv stay
# as the pre-sync record.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source deploy/iceberg_rest/env.rest

export MOBILITYDUCK__EXTENSION_PATH="${MOBILITYDUCK__EXTENSION_PATH:?set it in .env or export it}"
export TRIPS_DEST="${TRIPS_DEST:-s3://warehouse/trips}"

PYTHON=".venv/bin/python"
LAYOUTS="${LAYOUTS:-L0,L2_daily,L3_daily,L2s,L3s}"
ITERS="${ITERS:-5}"
LOG="logs/rebuild_experiments.log"
mkdir -p logs

say() { echo "$*" | tee -a "$LOG"; }
die() { say "FAILED: $*"; exit 1; }

say "=== stage 3: register + experiments  (layouts: $LAYOUTS, iters: $ITERS) ==="

say "[register] Iceberg tables (all layouts, in place on s3)"
"$PYTHON" -m lakehouse.store.publish >>"$LOG" 2>&1 || die "iceberg_to_s3"
say "[register] DONE"

say "[iceberg] run_queries.py -> results/iceberg_synced.csv"
"$PYTHON" -m lakehouse.query.run_iceberg --layouts "$LAYOUTS" --iters "$ITERS" \
    --out results/iceberg_synced.csv >>"$LOG" 2>&1 || die "run_queries"
say "[iceberg] DONE"

say "[parquet] run_parquet.py -> results/parquet_synced.csv"
"$PYTHON" -m lakehouse.query.run_parquet --layouts "$LAYOUTS" --iters "$ITERS" \
    --out results/parquet_synced.csv >>"$LOG" 2>&1 || die "run_parquet"
say "[parquet] DONE"

say "[compare] synced vs pre-sync"
"$PYTHON" scripts/compare_synced.py \
    --old results/iceberg.csv --new results/iceberg_synced.csv \
    --out results/tab_synced_vs_presync.csv 2>&1 | tee -a "$LOG"

say "=== stage 3 complete ==="
