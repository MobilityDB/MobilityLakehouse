#!/usr/bin/env bash
# reproduce.sh — the planar benchmark of the lakehouse paper, from the DMA archives to its answers.
#
# For the days FROM..TO, both included, it builds the raw zone of every day not yet in it
# (ingest/raw_zone.sh), cleans, segments and writes L0 (planar/run_clean.sh), builds the eleven
# layouts (planar/40_order_layouts.sh, 41_part_layouts.sh, 42_compact_layouts.sh) and checks them
# (planar/92_check_layouts.sh), stops unless every layout answers each window with L0's vessels
# (planar/51_verify_answers.sh), measures what each layout's row-group statistics let a window skip
# and the storage each layout takes (planar/50_query_layouts.sh, 53_storage.py), registers them in
# an Iceberg REST catalog over MinIO and in DuckLake (planar/80_register.py,
# 81_register_ducklake.sh, the services of planar/iceberg/docker-compose.yml), answers the ten
# benchmark queries on every layout and window once
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
# built from them; STEPS ("ingest clean layouts check sensitivity segments answers pruning storage
# catalogs queries timing"), the steps this invocation runs, each reading what the step before it
# wrote; `cold` is a further step, the timing after dropping the page cache before every run, which
# needs `sudo -n tee /proc/sys/vm/drop_caches`; `mobilitydb` is another, the ten queries answered by
# MobilityDB in PostgreSQL over L0 and compared with L0's answers (planar/75_mobilitydb.py), which
# needs a PostgreSQL server carrying MobilityDB, PostGIS and pg_parquet, named by PGHOST, PGPORT,
# PGDATABASE and PGUSER; PYTHON (python3), the interpreter carrying the packages of
# planar/requirements.txt, which the catalogs step registers the layouts with.
set -euo pipefail

B="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FROM=${1:-2026-01-01}
TO=${2:-2026-01-31}
export ROOT=${ROOT:-$(cd "$B/.." && pwd)/data}
STEPS=${STEPS:-"ingest clean layouts check sensitivity segments answers pruning storage catalogs queries timing"}
LAYOUTS="L0 L0X L0Z L0H L1 L2 L3 L4 L1s L2s L3s L4s"

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
if want segments; then
  mkdir -p "$OUT"
  "$B/planar/duckdb.sh" -cmd "SET VARIABLE out = '$RUN'" -c ".read $B/planar/73_segment_table.sql" \
    > "$OUT/segment-table.txt"
fi
# 50 and 51 append to their tables, so each step starts its file afresh. The answer gate stops the
# run before a figure is read from a layout whose vessels differ from L0's.
if want answers; then
  mkdir -p "$OUT"; rm -f "$OUT/layout-answers.csv"
  VERIFY_OUT="$OUT/layout-answers.csv" "$B/planar/51_verify_answers.sh" | tee "$OUT/layout-answers.log"
  tail -1 "$OUT/layout-answers.log" | grep -q "ALL LAYOUTS AGREE" \
    || { echo "reproduce.sh: the answer gate failed, see $OUT/layout-answers.log" >&2; exit 1; }
fi
if want pruning; then
  mkdir -p "$OUT"; rm -f "$OUT/layout-pruning.csv"
  LAYOUT_PRUNING_OUT="$OUT/layout-pruning.csv" "$B/planar/50_query_layouts.sh" > /dev/null
fi
if want storage; then
  mkdir -p "$OUT"
  python3 "$B/planar/53_storage.py" > "$OUT/storage.csv"
fi
if want catalogs; then
  # The bind mounts exist before the services start, so they belong to this user, not to Docker
  mkdir -p "$ROOT/lakehouse-store/catalog" "$ROOT/lakehouse-store/minio" \
    "$ROOT/lakehouse-store/ducklake"
  docker compose -f "$B/planar/iceberg/docker-compose.yml" up -d
  for _ in $(seq 60); do
    curl -sf -o /dev/null http://localhost:8181/v1/config && break
    sleep 1
  done
  "${PYTHON:-python3}" "$B/planar/80_register.py" $LAYOUTS
  # A fresh DuckLake catalog: the twelve layouts are its whole content, and a catalog written by one
  # DuckLake extension version is never carried into another
  rm -f "$ROOT/lakehouse-store/ducklake/trips.ducklake" "$ROOT/lakehouse-store/ducklake/trips.ducklake.wal"
  "${PYTHON:-python3}" -c "import s3fs; fs = s3fs.S3FileSystem(key='admin', secret='password', client_kwargs={'endpoint_url': 'http://localhost:9000'}); p = 'warehouse/ducklake/trips'; fs.rm(p, recursive=True) if fs.exists(p) else None"
  "$B/planar/81_register_ducklake.sh" $LAYOUTS
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
if want mobilitydb; then
  mkdir -p "$OUT"
  MOBILITYDB_OUT="$OUT/mobilitydb-answers.csv" python3 "$B/planar/75_mobilitydb.py" \
    --answers "$OUT/answers.csv"
fi
if want cold; then
  mkdir -p "$OUT"; rm -f "$OUT/query-runtime-cold.csv"
  QUERY_OUT="$OUT/query-runtime-cold.csv" python3 "$B/planar/70_queries.py" --mode cold
  python3 "$B/planar/71_summarize.py" "$OUT/query-runtime-cold.csv" \
    > "$OUT/query-runtime-cold-summary.csv"
fi
echo ">> $FROM..$TO done, run $RUN, results $OUT"
