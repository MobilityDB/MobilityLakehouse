#!/usr/bin/env bash
# The figures of the paper, checked against the ones a run produced.
#
#   93_check_figures.sh [FIGURES_DIR]
#
# FIGURES is the set the paper includes, and every name here is a file the paper carries as
# `\includegraphics{figures/<name>}`. A run that draws a figure under another name leaves the
# paper citing a file no reproduction produces, and nothing else in the pipeline notices: a figure
# is not a number, so no gate reads it. This check is that gate.
#
# Each name states the step that draws it, so a missing figure names the step to run rather than
# only the file that is absent. The check passes when every figure of a step that ran is there,
# and reports the steps that did not run rather than failing for them: `layout-figures` and
# `cover-figures` need QGIS, and a reviewer without it still reproduces the rest.
#
# Environment: ROOT (the repository's data/); FIGURES_DEST, the directory checked when no argument
# names one.
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
DIR=${1:-${FIGURES_DEST:-$ROOT/results/planar/figures}}

# A third field names a results table the figure needs: 74_figures.py draws the catalog figure only
# where the catalog steps have written theirs, so a run without them is complete without it and the
# check must not ask for it. A figure with no third field is drawn whenever its step runs.
#
# figure                          step              needs
FIGURES="
eval_month_pruning_bytes.png      figures
eval_month_speedup_heatmap.png    figures
eval_month_tradeoff.png           figures
eval_month_lakehouse.png          figures           catalog-pruning.csv
ds_spatial_heatmap.png            dataset-figures
inorder.png                       dataset-figures
L2_tiling_wide.png                layout-figures
L2_tiling_zoom.png                layout-figures
L3_tiling_wide.png                layout-figures
L3_tiling_zoom.png                layout-figures
segment_raw.png                   layout-figures
clean_segmented.png               layout-figures
h3cover_trip_res8.png             cover-figures
h3cover_trip_res9.png             cover-figures
h3cover_region_res8.png           cover-figures
h3cover_region_res9.png           cover-figures
"

[ -d "$DIR" ] || { echo "93_check_figures.sh: no figures directory $DIR" >&2; exit 1; }

declare -A present_of seen_of missing_of
while read -r fig step needs; do
  [ -n "$fig" ] || continue
  # A figure whose results table the run did not write is not one this run owes
  [ -z "$needs" ] || [ -s "$(dirname "$DIR")/$needs" ] || continue
  seen_of[$step]=$(( ${seen_of[$step]:-0} + 1 ))
  if [ -s "$DIR/$fig" ]; then
    present_of[$step]=$(( ${present_of[$step]:-0} + 1 ))
  else
    missing_of[$step]="${missing_of[$step]:-}$fig "
  fi
done <<< "$FIGURES"

# The report is collected and printed afterwards rather than piped through `sort`: a pipeline runs
# its left side in a subshell, where a status set inside the loop is lost and the check can only
# ever pass.
status=0
report=()
undrawn=()
for step in "${!seen_of[@]}"; do
  have=${present_of[$step]:-0}
  want=${seen_of[$step]}
  if [ "$have" -eq "$want" ]; then
    report+=("  $step: $have/$want")
  elif [ "$have" -eq 0 ]; then
    report+=("  $step: not run ($want figures)")
  else
    # Only a step that ran is held to its figures: a step nobody asked for is missing all of them
    # by definition, and naming those beside a real one buries it.
    report+=("  $step: $have/$want INCOMPLETE")
    for f in ${missing_of[$step]}; do undrawn+=("$f  ($step)"); done
    status=1
  fi
done
printf '%s\n' "${report[@]}" | sort

if [ "$status" -ne 0 ]; then
  echo "93_check_figures.sh: a step that ran left a figure of the paper undrawn:" >&2
  printf '  %s\n' "${undrawn[@]}" >&2
  exit 1
fi
echo "figures: every figure of every step that ran is in $DIR"
