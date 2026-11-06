#!/usr/bin/env bash
# The sensitivity layouts of the planar corpus: the spatial layouts rebuilt with one of the
# parameters the eleven hold fixed changed, each variant a run of its own under
# $RUN/sensitivity/<variant>, holding a link to the base run's L0 and a copy of its grid, so its
# layouts keep their names and every reading step reads it with RUN set to the variant:
#
#   cell25, cell100   L2, L3 and L3s on 25 and 100 km region cells (the base is 50 km);
#   shift             L2, L3 and L3s on the 50 km grid moved by half a cell along both axes;
#   nseg16, nseg32,   L3 and L3s grouping 16, 32 or 128 segments into one box (the base is 64);
#   nseg128
#   bin1h, bin6h      L3 whose pieces are also cut at one-hour or six-hour bins, the bin a third
#                     partition key.
#
# The daily layouts are 41_part_layouts.sh's and the compact ones 42_compact_layouts.sh's, with
# CELL_SIZE, GRID_SHIFT, ADAPTIVE_NSEG and L3_BIN_SECONDS set as the variant says; each variant's
# build is logged to log/planar-sensitivity.tsv as #run of 43_ablation_layouts.sh logs a layout.
#
#   RUN=data/stage/planar/2026-01-01_2026-02-01 benchmark/planar/44_sensitivity_layouts.sh [cell25 shift ...]
#
# Environment: RUN (required, the base run, whose L0 and grid.txt exist); ROOT (the repository's
# data/), the tree holding log/ and tmp/; DUCKDB; LAYOUT_MEMORY (12GB); LAYOUT_THREADS (4).
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
BASE=${RUN:?RUN is required}
export ROOT
[ -d "$BASE/L0" ] || { echo "no L0 under $BASE" >&2; exit 1; }
[ -s "$BASE/grid.txt" ] || { echo "no grid at $BASE/grid.txt: run 40_order_layouts.sh first" >&2; exit 1; }
mkdir -p "$ROOT/log"

# Builds variant $1: the daily layers $2 with the settings $3 (NAME=value ...), then the compact
# layouts of the layers $4
variant() {
  local name="$1" layers="$2" settings="$3" compact="$4" run="$BASE/sensitivity/$1" t0
  rm -rf "$run"
  mkdir -p "$run"
  ln -s "$BASE/L0" "$run/L0"
  cp "$BASE/grid.txt" "$run/grid.txt"
  t0=$(date +%s)
  env $settings RUN="$run" LAYERS="$layers" "$P/41_part_layouts.sh" > "$run/build.log"
  [ -z "$compact" ] || env $settings RUN="$run" "$P/42_compact_layouts.sh" $compact >> "$run/build.log"
  printf 'SENSITIVITY\t%s\t%s\t%ss\t%s files\t%s bytes\n' "$name" "$settings" "$(( $(date +%s) - t0 ))" \
    "$(find "$run/layouts_daily" "$run/layout_compact" -name '*.parquet' 2>/dev/null | wc -l)" \
    "$(du -sbL "$run/layouts_daily" "$run/layout_compact" 2>/dev/null | awk '{s += $1} END {print s}')" \
    | tee -a "$ROOT/log/planar-sensitivity.tsv"
}

for V in ${@:-cell25 cell100 shift nseg16 nseg32 nseg128 bin1h bin6h}; do
  case "$V" in
    cell25)  variant "$V" "L2 L3" "CELL_SIZE=25000.0" "L3" ;;
    cell100) variant "$V" "L2 L3" "CELL_SIZE=100000.0" "L3" ;;
    shift)   variant "$V" "L2 L3" "GRID_SHIFT=25000.0" "L3" ;;
    nseg16)  variant "$V" "L3" "ADAPTIVE_NSEG=16" "L3" ;;
    nseg32)  variant "$V" "L3" "ADAPTIVE_NSEG=32" "L3" ;;
    nseg128) variant "$V" "L3" "ADAPTIVE_NSEG=128" "L3" ;;
    bin1h)   variant "$V" "L3" "L3_BIN_SECONDS=3600" "" ;;
    bin6h)   variant "$V" "L3" "L3_BIN_SECONDS=21600" "" ;;
    *) echo "unknown sensitivity variant $V" >&2; exit 1 ;;
  esac
done
