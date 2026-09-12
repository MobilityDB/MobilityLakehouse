#!/usr/bin/env bash
# fetch_dma.sh — download the Danish Maritime Authority's daily AIS archives for a period.
#
# DMA publishes one zip per day at http://aisdata.ais.dk/aisdk-YYYY-MM-DD.zip. The data are DMA's
# and are not redistributed with this repository: this script fetches them from DMA onto the
# machine that runs the benchmark.
#
# A download goes to <name>.part and is renamed only once curl finishes cleanly, so an interrupted
# run never leaves a partial zip that raw_zone.sh would read as complete. A day whose zip, or whose
# raw-zone Parquet, is already there is skipped, so a run resumes where it stopped.
#
# Usage:
#   fetch_dma.sh FROM TO [--dir DIR] [--raw DIR] [--check]
#
#   FROM, TO   the first and the last day, both included, as YYYY-MM-DD: 2026-01-01 2026-01-31 is
#              the month the paper measures, 2026-01-15 2026-01-21 one week of it
#   --dir DIR  where the zips go                                  (default: the repository's data/)
#   --raw DIR  the raw zone; a day already built there is skipped (default: <dir>/raw)
#   --check    ask DMA for each day's size and print the total, downloading nothing
set -euo pipefail

BASE_URL="http://aisdata.ais.dk"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)/data"
RAW=""
CHECK=0

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' >&2; exit 2; }

[[ $# -ge 2 ]] || usage
FROM="$1"; TO="$2"; shift 2
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)   DIR="$2"; shift 2 ;;
    --raw)   RAW="$2"; shift 2 ;;
    --check) CHECK=1;  shift ;;
    *) echo "fetch_dma.sh: unknown option: $1" >&2; usage ;;
  esac
done
[[ -n "$RAW" ]] || RAW="$DIR/raw"

for day in "$FROM" "$TO"; do
  [[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || {
    echo "fetch_dma.sh: not a YYYY-MM-DD day: $day" >&2; exit 2; }
done
[[ ! "$FROM" > "$TO" ]] || { echo "fetch_dma.sh: FROM $FROM is after TO $TO" >&2; exit 2; }

# The day after $1, with GNU date or, on macOS, BSD date.
next_day() {
  date -u -d "$1 + 1 day" +%F 2>/dev/null || date -u -j -v+1d -f %F "$1" +%F
}

mkdir -p "$DIR"
n=0; got=0; skipped=0; total=0
d="$FROM"
while [[ ! "$d" > "$TO" ]]; do
  name="aisdk-$d.zip"
  if [[ "$CHECK" == 1 ]]; then
    size=$(curl -fsSI --max-time 60 "$BASE_URL/$name" 2>/dev/null | tr -d '\r' \
             | awk 'tolower($1) == "content-length:" {print $2}' || true)
    [[ -n "$size" ]] || { echo "fetch_dma.sh: DMA has no $name" >&2; exit 1; }
    printf '%s %s bytes\n' "$name" "$size"
    total=$((total + size))
  elif [[ -s "$DIR/$name" || -s "$RAW/aisdk-$d.parquet" ]]; then
    skipped=$((skipped + 1))
  else
    echo "fetching $name"
    curl -fsS --retry 3 --max-time 3600 -o "$DIR/$name.part" "$BASE_URL/$name" || {
      rm -f "$DIR/$name.part"; echo "fetch_dma.sh: the download of $name failed" >&2; exit 1; }
    mv "$DIR/$name.part" "$DIR/$name"
    got=$((got + 1))
  fi
  n=$((n + 1))
  d=$(next_day "$d")
  [[ -n "$d" ]] || { echo "fetch_dma.sh: cannot step past $name with this date" >&2; exit 1; }
done

if [[ "$CHECK" == 1 ]]; then
  awk -v n="$n" -v t="$total" 'BEGIN { printf "%d days, %.2f GB\n", n, t / 1e9 }'
else
  echo "$n days: $got downloaded, $skipped already present in $DIR or $RAW"
fi
