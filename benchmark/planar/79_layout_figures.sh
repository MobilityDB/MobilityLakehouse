#!/usr/bin/env bash
# The paper's tiling and segmentation figures, from a run to the PNGs: the stored bounds of the
# regular and the adaptive tiling over the grid they cut on (layoutfig/01_export.sql), and one
# vessel's day before and after cleaning and segmentation; one trip as each tiling cuts it
# (02_trip_export.sql); one ferry crossing as L0 and L3 store it and what the query of both ports
# does with each piece (03_crossing_export.sql); and the month of the run against the few L3 files
# one query reads (04_usefulness_export.sql, usefulness_grid.py).
#
#   ROOT=<data> RUN=<run> ./79_layout_figures.sh [DAY]
#
# The layers come out of the run with DuckDB alone: a segment's stored bounds and its tile are
# plain columns, and a segment's trajectory is exactly the cleaned positions of its vessel inside
# its own time span, so 01_export.sql decodes no trajectory; the trip, crossing and usefulness
# exports do, through MobilityDuck, and none needs PostgreSQL. QGIS then draws the layers over an
# OpenStreetMap basemap with coverfig/render_map.py.
#
# Environment: ROOT (the repository's data/); RUN (the run under ROOT/stage/planar/); RAW
# (ROOT/raw); DUCKDB_ENGINE, a DuckDB shell whose spatial extension writes the GeoPackages;
# VESSEL, the MMSI the segmentation figure draws; TRIP_VESSEL, the MMSI the one-trip tiling
# figures draw; CROSSING_VESSEL, CROSSING_T0 and CROSSING_T1, the ferry and the time span of the
# crossing the query-trip figures draw; USE_WINDOW, the window of windows_25832.csv the usefulness
# figure queries; ADAPTIVE_NSEG (64) and CELL_SIZE (50000), 41_part_layouts.sh's own,
# which the region grid is asked for; BOX_FRAME, the frame the segment boxes are cut to
# before export, the wide figure's own frame by default; STAGE and STAGE_QGIS, where the layers and
# the PNGs live, as this shell and as QGIS name them; FIGURES_DEST; QGIS_PYTHON, as
# layoutfig/render.sh documents it.
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
L="$P/layoutfig"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
RUN=${RUN:?RUN is required, the run under $ROOT/stage/planar/}
RAW=${RAW:-$ROOT/raw}
DAY=${1:-${DAY:-2026-01-15}}
VESSEL=${VESSEL:-257584000}
TRIP_VESSEL=${TRIP_VESSEL:-538011655}
CROSSING_VESSEL=${CROSSING_VESSEL:-219000431}
CROSSING_T0=${CROSSING_T0:-2026-01-15 17:19:00}
CROSSING_T1=${CROSSING_T1:-2026-01-15 18:10:00}
USE_WINDOW=${USE_WINDOW:-belt_1day}
NSEG=${ADAPTIVE_NSEG:-64}
export STAGE=${STAGE:-$ROOT/stage/layoutfig}
export FIGURES_DEST=${FIGURES_DEST:-$ROOT/results/planar/figures}

# The boxes are cut before export because a day of the corpus holds far more segments than a frame
# shows, and a layer QGIS never draws still costs the write and the read.
read -r BX0 BY0 BX1 BY1 <<< "${BOX_FRAME:-600000 6013000 700000 6088000}"

# The cell the region grid is asked for is the one 41_part_layouts.sh splits on, so the figure's
# grid and the layouts it draws cannot come apart.
CELL=${CELL_SIZE:-50000.0}

mkdir -p "$STAGE" "$FIGURES_DEST"

# The export asks the tiler for the region cells, so the engine has to carry MobilityDuck. A plain
# DuckDB reaches the call and answers `Table Function with name spacetiles does not exist! Did you
# mean "shapefile_meta"?`, which names neither the extension nor the variable that selects it.
if ! "$P/duckdb.sh" -c "SELECT count(*) FROM spaceTiles(
       stbox('SRID=25832;STBOX X((0,0),(100000,100000))'), 50000.0, 50000.0)" > /dev/null 2>&1; then
  echo "79_layout_figures.sh: the DuckDB this runs answers no spaceTiles, so it carries no" \
       "MobilityDuck; name one in DUCKDB_ENGINE or in planar/engine.path" >&2
  exit 1
fi

"$P/duckdb.sh" \
  -cmd "SET VARIABLE out = '$RUN'" -cmd "SET VARIABLE raw = '$RAW'" \
  -cmd "SET VARIABLE stage = '$STAGE'" -cmd "SET VARIABLE windows = '$P/windows_25832.csv'" \
  -cmd "SET VARIABLE day = '$DAY'" -cmd "SET VARIABLE vessel = $VESSEL" \
  -cmd "SET VARIABLE cell = $CELL" \
  -cmd "SET VARIABLE fx0 = $BX0" -cmd "SET VARIABLE fy0 = $BY0" \
  -cmd "SET VARIABLE fx1 = $BX1" -cmd "SET VARIABLE fy1 = $BY1" \
  -c ".read $L/01_export.sql"

# The one-trip tiling figures cut the trip to their frame, which 01_export.sql has just written
"$P/duckdb.sh" \
  -cmd "SET VARIABLE out = '$RUN'" -cmd "SET VARIABLE stage = '$STAGE'" \
  -cmd "SET VARIABLE day = '$DAY'" -cmd "SET VARIABLE trip_vessel = $TRIP_VESSEL" \
  -cmd "SET VARIABLE cell = $CELL" -cmd "SET VARIABLE nseg = $NSEG" \
  -cmd "SET VARIABLE margin = 200" \
  -c ".read $L/02_trip_export.sql"

"$P/duckdb.sh" \
  -cmd "SET VARIABLE out = '$RUN'" -cmd "SET VARIABLE stage = '$STAGE'" \
  -cmd "SET VARIABLE windows = '$P/windows_25832.csv'" -cmd "SET VARIABLE cell = $CELL" \
  -cmd "SET VARIABLE crossing_vessel = $CROSSING_VESSEL" \
  -cmd "SET VARIABLE crossing_t0 = '$CROSSING_T0'" -cmd "SET VARIABLE crossing_t1 = '$CROSSING_T1'" \
  -cmd "SET VARIABLE small = 100" \
  -c ".read $L/03_crossing_export.sql"

"$P/duckdb.sh" \
  -cmd "SET VARIABLE out = '$RUN'" -cmd "SET VARIABLE stage = '$STAGE'" \
  -cmd "SET VARIABLE windows = '$P/windows_25832.csv'" \
  -cmd "SET VARIABLE use_window = '$USE_WINDOW'" \
  -c ".read $L/04_usefulness_export.sql"

bash "$L/render.sh"
python3 "$L/usefulness_grid.py" "$STAGE/use_files.csv" \
  "$FIGURES_DEST/usefulness_files.png" "$FIGURES_DEST/usefulness_admitted.png"
echo ">> tiling, segmentation, query-trip and usefulness figures in $FIGURES_DEST"
