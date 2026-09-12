#!/usr/bin/env bash
# raw_zone.sh — build the raw zone: one Parquet file per day of DMA AIS CSV.
#
# The Danish Maritime Authority publishes one zip per day, each holding a single CSV named
# after the day. This script converts each in turn through raw_zone.sql, which owns the
# column contract, and writes ZSTD Parquet partitioned by day.
#
# One day at a time, and the decompressed CSV is deleted as soon as its Parquet lands: a
# month of AIS is ~87 GB of CSV against ~12 GB of Parquet, so holding one day (~2.8 GB)
# instead of the month is the difference between fitting on a laptop and not.
#
# RESUMABLE. A day whose Parquet already exists is skipped, so an interrupted run continues
# where it stopped rather than starting over. Delete a day's Parquet to force its rebuild.
#
# Each day logs its row count and wall time to stdout and to the run log; those lines are
# the build-cost figures the evaluation quotes, so they are emitted even when quiet.
#
# Usage:
#   raw_zone.sh --zips DIR [--out DIR] [--stage DIR] [--log FILE] [--days N]
#
# Options:
#   --zips   DIR   directory holding aisdk-YYYY-MM-DD.zip  (required)
#   --pattern GLOB which archives in --zips to build        (default: aisdk-*.zip)
#                  A study covers a stated period while a download directory holds whatever
#                  else has been fetched into it, so naming the period is what keeps a stray
#                  day out of the zone and the dataset the size the paper says it is.
#   --out    DIR   raw-zone Parquet destination            (default: the repository's data/raw)
#   --stage  DIR   scratch for the decompressed CSV        (default: the repository's data/stage)
#   --log    FILE  run log                                 (default: <out>/../log/raw_zone.log)
#   --days   N     stop after N days, for a smoke run      (default: all)
#   --duckdb PATH  duckdb binary                           (default: duckdb from PATH)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL="${SCRIPT_DIR}/raw_zone.sql"
# The repository's data directory, which holds the DMA archives and everything built from them.
DATA="$(cd "${SCRIPT_DIR}/../.." && pwd)/data"

ZIPS=""
PATTERN="aisdk-*.zip"
OUT="${DATA}/raw"
STAGE="${DATA}/stage"
LOG=""
DAYS=0
DUCKDB="${DUCKDB:-duckdb}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --zips)    ZIPS="$2";    shift 2 ;;
    --pattern) PATTERN="$2"; shift 2 ;;
    --out)    OUT="$2";    shift 2 ;;
    --stage)  STAGE="$2";  shift 2 ;;
    --log)    LOG="$2";    shift 2 ;;
    --days)   DAYS="$2";   shift 2 ;;
    --duckdb) DUCKDB="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$ZIPS" ]] || { echo "raw_zone.sh: --zips is required" >&2; exit 2; }
[[ -d "$ZIPS" ]] || { echo "raw_zone.sh: no such directory: $ZIPS" >&2; exit 2; }
[[ -r "$SQL"  ]] || { echo "raw_zone.sh: cannot read $SQL" >&2; exit 2; }
command -v "$DUCKDB" >/dev/null 2>&1 || [[ -x "$DUCKDB" ]] || {
  echo "raw_zone.sh: duckdb not found: $DUCKDB" >&2; exit 2; }

mkdir -p "$OUT" "$STAGE"
[[ -n "$LOG" ]] || LOG="$(cd "$OUT/.." && pwd)/log/raw_zone.log"
mkdir -p "$(dirname "$LOG")"

say() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

version=$("$DUCKDB" -noheader -list -c "SELECT version()" 2>/dev/null | tr -d '\n')
say "raw-zone build starting: zips=$ZIPS pattern=$PATTERN out=$OUT duckdb=${version:-unknown}"

n=0
for zip in "$ZIPS"/$PATTERN; do
  [[ -e "$zip" ]] || { say "no archive matching $PATTERN under $ZIPS"; exit 1; }
  day="$(basename "$zip" .zip)"; day="${day#aisdk-}"
  parquet="$OUT/aisdk-${day}.parquet"

  if [[ -s "$parquet" ]]; then
    say "skip  $day  (already built: $(du -h "$parquet" | cut -f1))"
    continue
  fi

  (( DAYS > 0 && n >= DAYS )) && { say "stopping after $DAYS day(s) as requested"; break; }
  n=$((n + 1))

  csv="$STAGE/aisdk-${day}.csv"
  start=$(date +%s)
  unzip -p "$zip" > "$csv"

  # A partial Parquet is worse than none: it looks built and the run would skip it. Write to
  # a temporary name and move it into place only once DuckDB has exited cleanly.
  tmp="$parquet.partial"
  printf "SET VARIABLE csv_path = '%s';\n.read %s\nCOPY ais_raw TO '%s' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 1000000);\n" \
      "$csv" "$SQL" "$tmp" | "$DUCKDB"
  mv "$tmp" "$parquet"
  rm -f "$csv"

  rows=$("$DUCKDB" -noheader -list -c "SELECT count(*) FROM read_parquet('$parquet')")
  elapsed=$(( $(date +%s) - start ))
  say "built $day  rows=$rows  parquet=$(du -h "$parquet" | cut -f1)  ${elapsed}s"
done

total=$("$DUCKDB" -noheader -list -c "SELECT count(*) FROM read_parquet('$OUT/aisdk-*.parquet')" 2>/dev/null || echo "?")
say "raw zone complete: $(ls "$OUT"/aisdk-*.parquet 2>/dev/null | wc -l) day(s), $total rows, $(du -sh "$OUT" | cut -f1)"
