#!/usr/bin/env bash
# reproduce.sh — the planar benchmark of the lakehouse paper, from the DMA archives to its answers.
#
# For the days FROM..TO, both included, it builds the raw zone of every day not yet in it
# (ingest/raw_zone.sh), cleans, segments and writes L0 (planar/run_clean.sh), builds the eleven
# layouts (planar/40_order_layouts.sh, 41_part_layouts.sh, 42_compact_layouts.sh) and checks them
# (planar/92_check_layouts.sh), answers the ten benchmark queries on every layout and window once
# and derives the answers table and each layout's recall against L0 (planar/70_queries.py,
# 72_answers.py), and times them, five warm runs each, summarized per group
# (planar/70_queries.py --mode warm, 71_summarize.py). The archives are the Danish Maritime
# Authority's and are fetched onto this machine first, with ingest/fetch_dma.sh; this script stops
# and names that command when a day of the period is neither downloaded nor already in the raw zone.
#
#   benchmark/ingest/fetch_dma.sh 2026-01-01 2026-01-31
#   benchmark/reproduce.sh 2026-01-01 2026-01-31
#
# FROM and TO default to 2026-01-01 and 2026-01-31, the month the paper measures;
# 2026-01-15 2026-01-21 is one week of it. The run is data/stage/planar/<FROM>_<TO + 1 day>, its
# results data/results/planar/<FROM>_<TO + 1 day>.
#
# Environment: DUCKDB_ENGINE, a DuckDB shell carrying MobilityDuck (else the one
# planar/engine.path names); ROOT (the repository's data/), which holds the archives and everything
# built from them; STEPS ("ingest clean layouts check sensitivity queries timing"), the steps this
# invocation runs, each reading what the step before it wrote; `cold` is a further step, the timing
# after dropping the page cache before every run, which needs `sudo -n tee /proc/sys/vm/drop_caches`.
set -euo pipefail

B="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FROM=${1:-2026-01-01}
TO=${2:-2026-01-31}
export ROOT=${ROOT:-$(cd "$B/.." && pwd)/data}
STEPS=${STEPS:-"ingest clean layouts check sensitivity queries timing"}

want() { case " $STEPS " in *" $1 "*) return 0;; *) return 1;; esac; }
# The day after $1, with GNU date or, on macOS, BSD date.
next_day() { date -u -d "$1 + 1 day" +%F 2>/dev/null || date -u -j -v+1d -f %F "$1" +%F; }

for day in "$FROM" "$TO"; do
  [[ "$day" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || {
    echo "reproduce.sh: not a YYYY-MM-DD day: $day" >&2; exit 2; }
done
[[ ! "$FROM" > "$TO" ]] || { echo "reproduce.sh: FROM $FROM is after TO $TO" >&2; exit 2; }
HI=$(next_day "$TO")
export RUN="$ROOT/stage/planar/${FROM}_${HI}"
OUT="$ROOT/results/planar/${FROM}_${HI}"

missing=()
d="$FROM"
while [[ ! "$d" > "$TO" ]]; do
  [[ -s "$ROOT/aisdk-$d.zip" || -s "$ROOT/raw/aisdk-$d.parquet" ]] || missing+=("$d")
  d=$(next_day "$d")
done
if (( ${#missing[@]} > 0 )); then
  echo "reproduce.sh: ${#missing[@]} day(s) of $FROM..$TO are neither in $ROOT nor in its raw zone," \
       "the first ${missing[0]}; download them from the Danish Maritime Authority with" >&2
  echo "  $B/ingest/fetch_dma.sh $FROM $TO --dir $ROOT" >&2
  exit 1
fi

echo ">> $FROM..$TO, run $RUN"
if want ingest; then
  d="$FROM"
  while [[ ! "$d" > "$TO" ]]; do
    if [[ ! -s "$ROOT/raw/aisdk-$d.parquet" ]]; then
      "$B/ingest/raw_zone.sh" --zips "$ROOT" --pattern "aisdk-$d.zip" --out "$ROOT/raw" \
        --stage "$ROOT/stage" --duckdb "$B/planar/duckdb.sh"
    fi
    d=$(next_day "$d")
  done
fi
if want clean; then
  LO="$FROM" HI="$HI" "$B/planar/run_clean.sh"
fi
if want layouts; then
  "$B/planar/40_order_layouts.sh"
  "$B/planar/41_part_layouts.sh"
  "$B/planar/42_compact_layouts.sh"
fi
if want check; then
  "$B/planar/92_check_layouts.sh"
fi
if want sensitivity; then
  mkdir -p "$OUT"
  (cd "$OUT" && "$B/planar/duckdb.sh" -cmd "SET VARIABLE out = '$RUN'" \
    -cmd "SET VARIABLE windows = '$B/planar/windows_25832.csv'" \
    -c ".read $B/planar/54_cell_sensitivity.sql")
fi
# 70_queries.py appends to QUERY_OUT, so each step starts its file afresh.
if want queries; then
  mkdir -p "$OUT"; rm -f "$OUT/query-answers.csv"
  QUERY_OUT="$OUT/query-answers.csv" python3 "$B/planar/70_queries.py" --mode warm --iter 1
  python3 "$B/planar/72_answers.py" "$OUT/query-answers.csv" \
    --answers "$OUT/answers.csv" --recall "$OUT/recall.csv"
fi
if want timing; then
  mkdir -p "$OUT"; rm -f "$OUT/query-runtime.csv"
  QUERY_OUT="$OUT/query-runtime.csv" python3 "$B/planar/70_queries.py" --mode warm
  python3 "$B/planar/71_summarize.py" "$OUT/query-runtime.csv" > "$OUT/query-runtime-summary.csv"
fi
if want cold; then
  mkdir -p "$OUT"; rm -f "$OUT/query-runtime-cold.csv"
  QUERY_OUT="$OUT/query-runtime-cold.csv" python3 "$B/planar/70_queries.py" --mode cold
  python3 "$B/planar/71_summarize.py" "$OUT/query-runtime-cold.csv" \
    > "$OUT/query-runtime-cold-summary.csv"
fi
echo ">> $FROM..$TO done, run $RUN, results $OUT"
