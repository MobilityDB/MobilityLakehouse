#!/usr/bin/env bash
# Export the layers of the cell-cover figures to GeoPackages under STAGE, where QGIS reads them.
#
# Each layer states its SRID and geometry type explicitly, since QGIS draws nothing from a layer
# without them, and each layer has a GeoPackage of its own, which QGIS reads concurrently.
set -euo pipefail

STAGE=${STAGE:?STAGE is required}
SOCK=${PGSOCK_DIR:?PGSOCK_DIR is required}
PORT=${PG_PORT:?PG_PORT is required}

command -v ogr2ogr > /dev/null || { echo "no ogr2ogr; install GDAL" >&2; exit 1; }
mkdir -p "$STAGE"
cd "$STAGE"
rm -f trip_line.gpkg trip_samples.gpkg trip_cells.gpkg region_polygon.gpkg region_cells.gpkg \
  grid.gpkg excerpt.gpkg
PG="PG:host=$SOCK port=$PORT user=postgres dbname=coverfig"
export PGTZ=UTC

ogr2ogr -f GPKG -a_srs EPSG:4326 -nlt LINESTRING trip_line.gpkg "$PG" -nln line -overwrite -sql \
  "SELECT (ST_Dump(Geom)).geom AS geom FROM FigTripLine"
ogr2ogr -f GPKG -a_srs EPSG:4326 -nlt POINT trip_samples.gpkg "$PG" -nln samples -overwrite -sql \
  "SELECT to_char(T, 'HH24:MI:SS') AS t, Geom AS geom FROM FigTripSamples"
ogr2ogr -f GPKG -a_srs EPSG:4326 -nlt POLYGON trip_cells.gpkg "$PG" -nln cells -overwrite -sql \
  "SELECT Res AS res, Class AS class, Cell::text AS cell, Geom AS geom FROM FigTripCells"

ogr2ogr -f GPKG -a_srs EPSG:4326 -nlt POLYGON region_polygon.gpkg "$PG" -nln polygon -overwrite \
  -sql "SELECT Id AS id, SiteId AS siteid, Geom AS geom FROM FigRegion"
ogr2ogr -f GPKG -a_srs EPSG:4326 -nlt POLYGON region_cells.gpkg "$PG" -nln cells -overwrite -sql \
  "SELECT Res AS res, Class AS class, Cell::text AS cell, Geom AS geom FROM FigRegionCells"

ogr2ogr -f GPKG -a_srs EPSG:4326 -nlt POLYGON grid.gpkg "$PG" -nln grid -overwrite -sql \
  "SELECT Fig AS fig, Cell::text AS cell, Geom AS geom FROM FigGrid"
ogr2ogr -f GPKG -a_srs EPSG:25832 -nlt POLYGON excerpt.gpkg "$PG" -nln excerpt -overwrite -sql \
  "SELECT f.Fig AS fig, e.Geom AS geom FROM FigFrame f JOIN FigFrame e ON e.Fig = f.Excerpt"

for f in trip_line trip_samples trip_cells region_polygon region_cells grid excerpt; do
  echo "$f: $(ogrinfo -so -al "$f.gpkg" | grep -E 'Feature Count')"
done
