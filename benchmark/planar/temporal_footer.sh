#!/usr/bin/env bash
# Print the COPY option that puts a TemporalParquet footer on a file of the query-ready zone.
#
#   temporal_footer.sh [SRID]        -> KV_METADATA {temporal: '{ ... }'}
#
# spec/temporalparquet.md 2.0.0 states the document: a `temporal` key whose value describes each
# temporal column, coexisting with GeoParquet's `geo`. The zone's temporal column is `trip`, a
# tgeompoint sequence in extended WKB, so a consumer reads the encoding, the base type, the
# interpolation and the frame from the footer without decoding a row.
#
# The CRS travels twice by that spec's own rule: `srid` names it and `crs` restates it as inline
# PROJJSON, the form GeoParquet's column metadata uses, so a reader resolves it against no
# registry. `crs` is optional, and a machine whose PROJ cannot state the PROJJSON emits the
# document without it rather than a `null` that would claim the frame is unknown when it is not.
#
# Every writer of the zone calls this, so the nine COPY statements carry one document and cannot
# drift from one another.
set -euo pipefail

SRID=${1:-${SRID:-25832}}

crs=""
if command -v projinfo > /dev/null; then
  # projinfo prints a `PROJJSON:` banner before the document
  j=$(projinfo "EPSG:$SRID" -o PROJJSON -q 2>/dev/null | sed '1{/^PROJJSON:/d}' || true)
  # a JSON object and nothing else; a single quote would end the SQL string that carries it
  case "$j" in
    "{"*"}") [ "${j#*\'}" = "$j" ] && crs=$(printf '%s' "$j" | tr -d '\n' | tr -s ' ') ;;
  esac
fi

doc="{\"version\":\"2.0.0\",\"primary_temporal_column\":\"trip\",\"columns\":{\"trip\":{"
doc="$doc\"encoding\":\"MEOS-WKB\",\"encoding_version\":\"1.0\","
doc="$doc\"base_type\":\"tgeompoint\",\"subtype\":\"Sequence\",\"interpolation\":\"linear\","
doc="$doc\"srid\":$SRID,"
[ -z "$crs" ] || doc="$doc\"crs\":$crs,"
doc="$doc\"edges\":\"planar\",\"geodetic\":false,\"has_z\":false}}}"

printf "KV_METADATA {temporal: '%s'}" "$doc"
