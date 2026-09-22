#!/usr/bin/env bash
# Start the private PostgreSQL cluster the cell-cover figures are computed in, loading MobilityDB
# (with H3) from the staged PostgreSQL prefix named by MOBILITYDB_PG_PREFIX, and create the
# database afresh with its extensions, so its catalog is the one that build installs.
#
# The cluster listens on no address and holds its socket in PGSOCK_DIR, so it is reachable only
# from this machine and cannot be confused with a server holding a benchmark run. 77_cover_figures.sh
# stops it however it ends; stopping it by hand is
#   pg_ctl -D "$PGDATA_DIR" stop -m fast
set -euo pipefail

BIN=${PG_BIN:?PG_BIN is required}
DATA=${PGDATA_DIR:?PGDATA_DIR is required}
SOCK=${PGSOCK_DIR:?PGSOCK_DIR is required}
PORT=${PG_PORT:?PG_PORT is required}
PFX=${MOBILITYDB_PG_PREFIX:?set MOBILITYDB_PG_PREFIX to the staged PostgreSQL prefix of a MobilityDB build}

[ -f "$PFX/share/extension/mobilitydb.control" ] ||
  { echo "no mobilitydb.control under $PFX/share/extension" >&2; exit 1; }

mkdir -p "$SOCK" "$(dirname "$DATA")"
[ -d "$DATA" ] || "$BIN/initdb" -D "$DATA" -U postgres -A trust > /dev/null

# extension_control_path and dynamic_library_path put the staged build ahead of anything the
# binaries' own prefix installs, so the figures are computed by the MobilityDB named here.
"$BIN/pg_ctl" -D "$DATA" -l "$(dirname "$DATA")/pg.log" -w start -o \
  "-p $PORT -c listen_addresses= -k $SOCK -c shared_buffers=1GB -c work_mem=256MB \
   -c extension_control_path=$PFX/share:\\\$system -c dynamic_library_path=$PFX/lib:\\\$libdir"

PSQL=("$BIN/psql" -X -v ON_ERROR_STOP=1 -h "$SOCK" -p "$PORT" -U postgres -d postgres)
"${PSQL[@]}" -c "DROP DATABASE IF EXISTS coverfig"
"${PSQL[@]}" -c "CREATE DATABASE coverfig"
"$BIN/psql" -X -v ON_ERROR_STOP=1 -h "$SOCK" -p "$PORT" -U postgres -d coverfig \
  -c "CREATE EXTENSION mobilitydb CASCADE"
"$BIN/psql" -X -h "$SOCK" -p "$PORT" -U postgres -d coverfig -Atc \
  "SELECT 'loaded ' || mobilitydbFullVersion() || ' from ' || setting
   FROM pg_settings WHERE name = 'dynamic_library_path'"
