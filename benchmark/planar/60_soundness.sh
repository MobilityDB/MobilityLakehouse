#!/usr/bin/env bash
# The cell cover's soundness at each resolution named: planar/cellcover/cellcover over the run's
# moving trips (46_trips.sh) and the sixteen windows of windows.psv (45_windows.sql), all in WGS84:
# the trips are the corpus trips transformed to EPSG:4326 instant by instant, the windows the
# corpus rectangles transformed corner by corner. Truth is the exact predicate on those trips and
# windows; the covers are H3's, over the same trips and windows. Each resolution writes
# soundness-res<N>.csv, its log and, while it runs, a checkpoint of its running totals.
#
#   RUN=<run> ./60_soundness.sh 7 8 9 10 11 12
#
# Environment: RUN (required); ROOT (the repository's data/); SOUNDNESS_BIN (cellcover/cellcover
# beside this script); SOUNDNESS_OUT ($ROOT/results/planar); SOUNDNESS_MEMORY_MAX (8G), the memory
# a resolution may take where a user systemd session can hold it to that.
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
RUN=${RUN:?RUN is required}
BIN=${SOUNDNESS_BIN:-$P/cellcover/cellcover}
WINDOWS="$P/windows.psv"
OUT=${SOUNDNESS_OUT:-$ROOT/results/planar}
MEM=${SOUNDNESS_MEMORY_MAX:-8G}
mkdir -p "$OUT"

[ -x "$BIN" ] || { echo "no such cover binary: $BIN; run planar/cellcover/build.sh <MEOS prefix>" >&2; exit 1; }
[ -s "$WINDOWS" ] || { echo "no windows at $WINDOWS" >&2; exit 1; }
echo "cover:     $BIN"
echo "  libmeos  $(ldd "$BIN" | awk '/libmeos/ {print $3}')"

# A resolution runs in a memory-capped scope where the user's systemd session provides one
CAP=()
if command -v systemd-run > /dev/null && systemd-run --user --scope -q true 2> /dev/null; then
  CAP=(systemd-run --user --scope -q -p MemoryMax="$MEM" -p MemorySwapMax=0)
fi

# The packed corpus is read where it exists; one older than the text corpus describes a corpus
# that has since been rebuilt, and is refused.
TRIPS="$RUN/trips.bin"
if [ -f "$TRIPS" ]; then
  if [ -f "$RUN/trips.csv" ] && [ "$RUN/trips.csv" -nt "$TRIPS" ]; then
    echo "trips.bin is older than trips.csv; run: $P/cellcover/trips_pack" \
         "$RUN/trips.csv $TRIPS" >&2
    exit 1
  fi
else
  TRIPS="$RUN/trips.csv"
fi
[ -f "$TRIPS" ] || { echo "no corpus at $TRIPS" >&2; exit 1; }
echo "positions: $TRIPS ($(date -r "$TRIPS" '+%F %T'))"

for res in "$@"; do
  echo "=== resolution $res  (load $(cut -d' ' -f1-3 /proc/loadavg))"
  # The run leaves its running totals in the checkpoint every 1000 trips and removes it once the
  # final block is written, so a checkpoint that outlives the run names an unfinished resolution.
  "${CAP[@]}" /usr/bin/time -f "  %e s  %M KB" \
      "$BIN" "$TRIPS" "$WINDOWS" "$res" "$OUT/soundness-res$res.checkpoint.csv" \
      > "$OUT/soundness-res$res.csv" 2> "$OUT/soundness-res$res.log" \
    || echo "  resolution $res did not finish; see the checkpoint"
  tail -3 "$OUT/soundness-res$res.log"
  cat "$OUT/soundness-res$res.csv"
done
