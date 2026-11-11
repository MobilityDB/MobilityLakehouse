#!/usr/bin/env bash
# The paper's two cell-cover figures, from the DMA archives to the PNGs: the cover of one vessel
# track (coverfig/05_trip_cells.sql) and the cover of one protected area (coverfig/06_region_cells.sql),
# each at H3 resolutions 8 and 9, drawn over an OpenStreetMap basemap by QGIS.
#
#   ROOT=<data> MOBILITYDB_PG_PREFIX=<prefix> ./77_cover_figures.sh
#
# These figures do not come from a benchmark run. They are computed in a PostgreSQL cluster of
# their own, from the raw DMA files of a few days and the WDPA protected areas, because they show
# what a cover IS rather than what a layout measures: the cells a traversal adds over the cells the
# recorded positions fall in, and the cells a region's boundary passes through over the cells it
# encloses. The cluster is private and is created and dropped by this script, so it cannot disturb
# a server holding a benchmark run.
#
# Environment: ROOT (the repository's data/); MOBILITYDB_PG_PREFIX (required), the staged
# PostgreSQL prefix of a MobilityDB build carrying H3, the <DESTDIR>/usr/local/pgsql/<major> of a
# DESTDIR install, whose catalog the cluster loads; AIS_ARCHIVES, the files the track is read
# from, the parquet files of the raw zone (data/raw/aisdk-2026-01-*.parquet) or raw DMA csv files;
# NATURAL_AREAS, the gzipped WDPA export the protected area is read from
# (data/archives/natural_areas.csv.gz); TRIP_MMSI (219020146) and TRIP_T0 (2026-01-28 11:05:01+00)
# the excursion drawn by 05_trip_cells.sql, the paper's Figure 5, a 7 m rigid inflatable boat
# off Frederikshavn, TRIP_T0 empty for the excursion of that vessel adding the most cells; PG_BIN, the PostgreSQL binaries
# (the prefix's own bin); PG_PORT (55453); FIGURES_DEST, where the PNGs are copied
# ($ROOT/results/planar/figures); QGIS_PYTHON, the interpreter carrying the QGIS Python API.
#
# The renders need QGIS. On Linux that is the system `python3` with python3-qgis installed; on
# Windows, from WSL, it is the QGIS bat, and QGIS_PYTHON is then the cmd.exe invocation of it, for
# example
#   QGIS_PYTHON='cmd.exe /c C:\Program Files\QGIS 3.40.15\bin\python-qgis-ltr.bat'
# in which case STAGE must be a path both sides can read, such as /mnt/c/Windows/Temp/h3cover.
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
C="$P/coverfig"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
export ROOT
export PG_PORT=${PG_PORT:-55453}
export PGDATA_DIR=${PGDATA_DIR:-$ROOT/stage/coverfig/pgdata}
export PGSOCK_DIR=${PGSOCK_DIR:-$ROOT/stage/coverfig/pgsock}
export STAGE=${STAGE:-$ROOT/stage/coverfig/layers}
export FIGURES_DEST=${FIGURES_DEST:-$ROOT/results/planar/figures}
export AIS_CSV=${AIS_CSV:-$ROOT/stage/coverfig/ais_frederikshavn.csv}

: "${MOBILITYDB_PG_PREFIX:?set MOBILITYDB_PG_PREFIX to the staged PostgreSQL prefix of a MobilityDB build carrying H3}"
export MOBILITYDB_PG_PREFIX
export PG_BIN=${PG_BIN:-$MOBILITYDB_PG_PREFIX/bin}
export AIS_ARCHIVES=${AIS_ARCHIVES:-$ROOT/raw/aisdk-2026-01-*.parquet}
export NATURAL_AREAS=${NATURAL_AREAS:-$ROOT/archives/natural_areas.csv.gz}
export TRIP_MMSI=${TRIP_MMSI:-219020146}
export TRIP_T0=${TRIP_T0-2026-01-28 11:05:01+00}

mkdir -p "$STAGE" "$FIGURES_DEST" "$(dirname "$AIS_CSV")"

PSQL=("$PG_BIN/psql" -X -v ON_ERROR_STOP=1 -h "$PGSOCK_DIR" -p "$PG_PORT" -U postgres -d coverfig)

bash "$C/00_cluster.sh"
# The cluster is this script's, so it goes down however this script ends
trap '"$PG_BIN/pg_ctl" -D "$PGDATA_DIR" stop -m fast > /dev/null 2>&1 || true' EXIT

bash "$C/01_prefilter_ais.sh"
for f in 02_natural_areas 03_ais 04_excursions 05_trip_cells 06_region_cells 07_frames; do
  echo "=== $f"
  "${PSQL[@]}" -v ais_csv="$AIS_CSV" -v natural_areas="$NATURAL_AREAS" \
    -v trip_mmsi="$TRIP_MMSI" -v trip_t0="$TRIP_T0" -f "$C/$f.sql"
done
bash "$C/08_export.sh"
bash "$C/render.sh"
echo ">> cover figures in $FIGURES_DEST"
