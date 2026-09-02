#!/usr/bin/env bash
# Full post-sync benchmark: all twelve layouts, --iters 5, both read paths.
#
# One layout per invocation, each writing its own CSV, then merged. run_queries.py
# only writes its --out at the very end, so a crash anywhere discards the whole
# run -- and it does crash: MobilityDuck's Temporal_derivative (reached through
# speed() in speed_profile) dereferences a NULL temporal in temporal_mem_size and
# takes the process down with SIGSEGV. It is not reliably reproducible, so each
# layout gets one retry, and a layout that still fails is reported and skipped
# rather than losing the eleven that worked.
#
# Writes *_full.csv; the earlier synced CSVs and the pre-sync originals stay intact.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source deploy/iceberg_rest/env.rest

export MOBILITYDUCK__EXTENSION_PATH="${MOBILITYDUCK__EXTENSION_PATH:?set it in .env or export it}"
export TRIPS_DEST="${TRIPS_DEST:-s3://warehouse/trips}"

PYTHON=".venv/bin/python"
LAYOUTS=(L0 L0X L0Z L0H L1_daily L1s L2_daily L2s L3_daily L3s L4_daily L4s)
ITERS="${ITERS:-5}"
LOG="logs/rerun_full.log"
PARTS="results/_parts"
mkdir -p logs "$PARTS"

say() { echo "$*" | tee -a "$LOG"; }

run_suite() {          # $1 = script, $2 = tag
    local script="$1" tag="$2" failed=()
    say "=== $tag: 12 layouts, iters=$ITERS ==="
    for L in "${LAYOUTS[@]}"; do
        local out="$PARTS/${tag}_${L}.csv"
        if [[ -s "$out" ]]; then say "  $L already done"; continue; fi
        local ok=0
        for attempt in 1 2; do
            if "$PYTHON" "scripts/$script" --layouts "$L" --iters "$ITERS" \
                   --out "$out" >>"$LOG" 2>&1 && [[ -s "$out" ]]; then
                ok=1; break
            fi
            say "  $L attempt $attempt failed (likely the MEOS segfault); retrying"
            rm -f "$out"
        done
        if (( ok )); then say "  $L OK"; else say "  $L GAVE UP"; failed+=("$L"); fi
    done
    "$PYTHON" - "$tag" <<'PY'
import sys, glob, pandas as pd
tag = sys.argv[1]
fs = sorted(glob.glob(f"results/_parts/{tag}_*.csv"))
if fs:
    d = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    d.to_csv(f"results/{tag}_synced.csv", index=False)
    print(f"merged {len(fs)} layouts -> results/{tag}_synced.csv ({len(d)} rows)")
PY
    (( ${#failed[@]} )) && say "  $tag layouts still missing: ${failed[*]}"
    return 0
}

run_suite run_queries.py iceberg
run_suite run_parquet.py parquet
say "=== full re-run complete ==="
