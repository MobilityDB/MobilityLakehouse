#!/usr/bin/env bash
# Pre-filter the raw DMA AIS files the track is read from, csv archives or the parquet files of
# the raw zone, to a box around Frederikshavn, keeping the columns the trip needs.
#
# The figures need one vessel over a few hours, so reading the archives once into a small csv
# costs less than loading a month into the figure cluster. Every column is read as VARCHAR and cast
# afterwards, because a DMA archive carries empty and malformed fields that a typed read rejects.
set -euo pipefail

OUT=${AIS_CSV:?AIS_CSV is required}
ARCHIVES=${AIS_ARCHIVES:?AIS_ARCHIVES is required}
DUCKDB=${DUCKDB_ENGINE:-duckdb}

mkdir -p "$(dirname "$OUT")"
command -v "$DUCKDB" > /dev/null ||
  { echo "no duckdb at $DUCKDB; set DUCKDB_ENGINE" >&2; exit 1; }

COLS="{'# Timestamp':'VARCHAR','Type of mobile':'VARCHAR','MMSI':'VARCHAR','Latitude':'VARCHAR',\
'Longitude':'VARCHAR','Navigational status':'VARCHAR','ROT':'VARCHAR','SOG':'VARCHAR',\
'COG':'VARCHAR','Heading':'VARCHAR','IMO':'VARCHAR','Callsign':'VARCHAR','Name':'VARCHAR',\
'Ship type':'VARCHAR','Cargo type':'VARCHAR','Width':'VARCHAR','Length':'VARCHAR',\
'Type of position fixing device':'VARCHAR','Draught':'VARCHAR','Destination':'VARCHAR',\
'ETA':'VARCHAR','Data source type':'VARCHAR','A':'VARCHAR','B':'VARCHAR','C':'VARCHAR','D':'VARCHAR'}"

# The parquet files of the raw zone (raw_zone.sh) carry the same fields typed and renamed
if [[ "$ARCHIVES" == *.parquet ]]; then
  "$DUCKDB" -c "
SET threads=12;
COPY (
  SELECT T AS t, TypeOfMobile AS mobile, MMSI AS mmsi, Latitude AS lat, Longitude AS lon, SOG AS sog,
         ShipType AS shiptype, Name AS name
  FROM read_parquet('$ARCHIVES')
  WHERE Latitude BETWEEN 57.30 AND 57.70 AND Longitude BETWEEN 10.30 AND 11.00
) TO '$OUT' (HEADER, DELIMITER ',');"
  wc -l "$OUT"
  exit 0
fi
"$DUCKDB" -c "
SET threads=12;
COPY (
  SELECT strptime(\"# Timestamp\", '%d/%m/%Y %H:%M:%S') AS t, \"Type of mobile\" AS mobile,
         MMSI AS mmsi, Latitude AS lat, Longitude AS lon, SOG AS sog,
         \"Ship type\" AS shiptype, Name AS name
  FROM read_csv('$ARCHIVES', header=true, all_varchar=true, ignore_errors=true, parallel=true,
                columns=$COLS)
  WHERE TRY_CAST(Latitude AS DOUBLE) BETWEEN 57.30 AND 57.70
    AND TRY_CAST(Longitude AS DOUBLE) BETWEEN 10.30 AND 11.00
) TO '$OUT' (HEADER, DELIMITER ',');"
wc -l "$OUT"
