#!/usr/bin/env bash
# The DuckDB every planar script runs: a DuckDB shell carrying MobilityDuck, the one
# $DUCKDB_ENGINE names, else the one non-comment line of engine.path beside this script (a local
# file naming the machine's build, never committed), started with the arrow lambda syntax disabled
# and the session time zone at UTC. A lambda is written `lambda x: ...` (`lambda acc, x: ...` for
# two parameters); an arrow `x -> ...`, deprecated since DuckDB 1.3 and off by default from 2.0,
# is then a parser error rather than a warning. AIS reports carry UTC time and the raw zone keeps
# it as a TIMESTAMP without zone, so every cast between TIMESTAMP and TIMESTAMPTZ (a trajectory
# instant built from a report, a day cut, a stored bound) reads it in UTC; MobilityDuck otherwise
# leaves the session at Europe/Brussels.
#
#   duckdb.sh [duckdb arguments ...]
set -euo pipefail

HERE=$(dirname "${BASH_SOURCE[0]}")
ENGINE=${DUCKDB_ENGINE:-}
if [ -z "$ENGINE" ] && [ -f "$HERE/engine.path" ]; then
  ENGINE=$(grep -v '^#' "$HERE/engine.path")
fi
[ -n "$ENGINE" ] || { echo "duckdb.sh: set DUCKDB_ENGINE to a DuckDB shell carrying MobilityDuck, or name one in $HERE/engine.path" >&2; exit 1; }
[ -x "$ENGINE" ] || { echo "duckdb.sh: no engine at $ENGINE" >&2; exit 1; }
[ "$ENGINE" -ef "${BASH_SOURCE[0]}" ] && { echo "duckdb.sh: DUCKDB_ENGINE names this wrapper" >&2; exit 1; }
exec "$ENGINE" -cmd "SET lambda_syntax = 'DISABLE_SINGLE_ARROW'" -cmd "SET TimeZone = 'UTC'" "$@"
