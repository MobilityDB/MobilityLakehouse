#!/usr/bin/env bash
# Render the tiling and segmentation figures with QGIS and copy them to FIGURES_DEST.
#
#   render.sh [L2_tiling_wide L3_tiling_wide L2_tiling_zoom L3_tiling_zoom segment_raw clean_segmented]
#
# This mirrors planar/coverfig/render.sh, whose renderer it reuses: the QGIS interpreter is named
# the same way, the jobs are written into STAGE beside the layers, and the renderer itself is
# coverfig/render_map.py rather than a copy of it.
#
# Environment: STAGE, holding the GeoPackages 01_export.sql writes; STAGE_QGIS, the same directory
# as the QGIS process names it, where that differs; FIGURES_DEST, where the PNGs are copied;
# QGIS_PYTHON, the command running an interpreter that carries the QGIS Python API, `python3` where
# python3-qgis is installed, and from WSL against a QGIS on the Windows host the bat, whose path
# needs its own quotes because it holds spaces:
#   QGIS_PYTHON='cmd.exe /c "C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat"'
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE=${STAGE:?STAGE is required}
DEST=${FIGURES_DEST:?FIGURES_DEST is required}
QGIS_PYTHON=${QGIS_PYTHON:-python3}
figs=${*:-L2_tiling_wide L3_tiling_wide L2_tiling_zoom L3_tiling_zoom segment_raw clean_segmented}

mkdir -p "$STAGE" "$DEST"
STAGE_QGIS=${STAGE_QGIS:-$STAGE} FRAMES="$STAGE/frames.csv" \
  "$(command -v python3)" "$HERE/make_jobs.py" "$STAGE" > /dev/null
cp "$HERE/../coverfig/render_map.py" "$STAGE/"
cd "$STAGE"

# QGIS_PYTHON is a command line, not a single word, and is split honouring shell quoting rather
# than on spaces, since the interpreter of a QGIS on the Windows host lives under `C:\Program Files`.
eval "QGIS_CMD=($QGIS_PYTHON)"
printf 'import qgis.core\nprint("QGIS", qgis.core.Qgis.QGIS_VERSION)\n' > qgis_version.py
if ! ver=$("${QGIS_CMD[@]}" qgis_version.py 2>&1 | grep '^QGIS '); then
  echo "render.sh: QGIS_PYTHON ($QGIS_PYTHON) carries no QGIS Python API." >&2
  cat >&2 <<'EOF'
  On Linux install QGIS and its Python bindings (python3-qgis) and leave QGIS_PYTHON unset.
  From WSL against a QGIS on the Windows host, name that bat and quote its path, which holds
  spaces, then stage where both sides read:
    QGIS_PYTHON='cmd.exe /c "C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat"'
    STAGE=/mnt/c/Windows/Temp/layoutfig STAGE_QGIS='C:\Windows\Temp\layoutfig'
EOF
  exit 1
fi
echo "renderer:  $ver"

for fig in $figs; do
  "${QGIS_CMD[@]}" render_map.py "job_$fig.json" 2>&1 |
    grep -E 'wrote|Error|Traceback|invalid|line [0-9]+' || true
  [ -s "$STAGE/$fig.png" ] || { echo "render.sh: no $STAGE/$fig.png" >&2; exit 1; }
  cp "$STAGE/$fig.png" "$DEST/$fig.png"
  echo "copied $DEST/$fig.png"
done
