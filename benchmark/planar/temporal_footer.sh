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
# `srid` names the CRS and is always written. `crs`, the same CRS again as inline PROJJSON, is
# written only under CRS_INLINE, for an SRID no registry resolves; a `null` is never written, since
# that states the frame is unknown when `srid` names it.
#
# Every writer of the zone calls this, so the nine COPY statements carry one document and cannot
# drift from one another.
set -euo pipefail

SRID=${1:-${SRID:-25832}}

# `crs` is written where it states what `srid` cannot. The specification defines its absence --
# "when `crs` is absent, the CRS is the one `srid` names" -- so a registered code needs no inline
# copy of its own definition, and a run writes one per file: 3,191 bytes against 252 for the rest
# of the document, 35 MB over a corpus of ten thousand files, all of it the same text. An SRID a
# registry does not resolve is the case the inline form exists for, and CRS_INLINE asks for it.
crs=""
if [ "${CRS_INLINE:-0}" = 1 ] && command -v projinfo > /dev/null; then
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
