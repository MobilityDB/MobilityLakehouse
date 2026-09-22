#!/usr/bin/env bash
# Render the four PNGs of the paper's two cell-cover figures with QGIS and copy them to
# FIGURES_DEST.
#
#   render.sh [trip_res8 trip_res9 region_res8 region_res9]
#
# Environment: STAGE, holding the GeoPackages 08_export.sh writes; STAGE_QGIS, the same directory
# as the QGIS process names it, where that differs (a QGIS on the Windows host reads
# C:\Windows\Temp\h3cover for the /mnt/c/Windows/Temp/h3cover this shell writes); FIGURES_DEST,
# where the PNGs are copied; QGIS_PYTHON, the command running an interpreter that carries the QGIS
# Python API, `python3` where python3-qgis is installed, and from WSL against a QGIS on the Windows
# host the bat, whose path needs its own quotes because it holds spaces:
#   QGIS_PYTHON='cmd.exe /c "C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat"'
#
# The job files are written into STAGE beside the layers, not into the checkout, because they name
# absolute paths of one machine and so are a product of a run rather than a source of the paper.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE=${STAGE:?STAGE is required}
DEST=${FIGURES_DEST:?FIGURES_DEST is required}
QGIS_PYTHON=${QGIS_PYTHON:-python3}
figs=${*:-trip_res8 trip_res9 region_res8 region_res9}

mkdir -p "$STAGE" "$DEST"
STAGE_QGIS=${STAGE_QGIS:-$STAGE} "$(command -v python3)" "$HERE/make_jobs.py" "$STAGE" > /dev/null
cp "$HERE/render_map.py" "$STAGE/"
cd "$STAGE"

# QGIS_PYTHON is a command line, not a single word, and it is split honouring shell quoting rather
# than on spaces: the interpreter of a QGIS on the Windows host lives under `C:\Program Files`, so
# splitting on spaces alone would pass `C:\Program` as the command and drop the rest.
eval "QGIS_CMD=($QGIS_PYTHON)"

# QGIS_CMD is asked for the QGIS version before a render, because without it the first failure is a
# ModuleNotFoundError raised inside render_map.py, which names the missing import and not the
# interpreter that lacks it. A plain python3 carries the QGIS API only where python3-qgis is
# installed, and the message below is what says so.
printf 'import qgis.core\nprint("QGIS", qgis.core.Qgis.QGIS_VERSION)\n' > qgis_version.py
if ! ver=$("${QGIS_CMD[@]}" qgis_version.py 2>&1 | grep '^QGIS '); then
  echo "render.sh: QGIS_PYTHON ($QGIS_PYTHON) carries no QGIS Python API." >&2
  cat >&2 <<'EOF'
  On Linux install QGIS and its Python bindings (python3-qgis) and leave QGIS_PYTHON unset.
  From WSL against a QGIS on the Windows host, name that bat and quote its path, which holds
  spaces, then stage where both sides read:
    QGIS_PYTHON='cmd.exe /c "C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat"'
    STAGE=/mnt/c/Windows/Temp/h3cover STAGE_QGIS='C:\Windows\Temp\h3cover'
EOF
  exit 1
fi
echo "renderer:  $ver"

for fig in $figs; do
  "${QGIS_CMD[@]}" render_map.py "job_$fig.json" 2>&1 |
    grep -E 'wrote|Error|Traceback|invalid|line [0-9]+' || true
  [ -s "$STAGE/h3cover_$fig.png" ] || { echo "render.sh: no $STAGE/h3cover_$fig.png" >&2; exit 1; }
  cp "$STAGE/h3cover_$fig.png" "$DEST/h3cover_$fig.png"
  echo "copied $DEST/h3cover_$fig.png"
done
