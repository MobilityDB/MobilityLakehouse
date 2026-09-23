#!/usr/bin/env bash
# The TemporalParquet footer of the query-ready zone, checked against the files a run wrote.
#
#   94_check_footer.sh [RUN]
#
# spec/temporalparquet.md states that a file carrying a temporal column carries a `temporal` key in
# its Parquet footer describing that column. A file without one is read only by a consumer that
# already knows what the blob is, which is the opposite of what the specification is for, and
# nothing else in the pipeline notices: the footer is metadata, so every answer, every recall and
# every timing is identical with it and without it. This check is what notices.
#
# One file of L0 and one of each layout is read, since a layout's files are written by one COPY.
#
# Environment: ROOT (the repository's data/); RUN (the run under ROOT/stage/planar/); DUCKDB_ENGINE.
set -euo pipefail

P="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=${ROOT:-$(cd "$P/../.." && pwd)/data}
RUN=${1:-${RUN:?RUN is required, the run under $ROOT/stage/planar/}}
D=${DUCKDB_ENGINE:-duckdb}

status=0
checked=0
for dir in "$RUN/L0" "$RUN/layouts_daily"/* "$RUN/layout_compact"/*; do
  [ -d "$dir" ] || continue
  f=$(find "$dir" -name '*.parquet' -print -quit 2>/dev/null)
  [ -n "$f" ] || continue
  checked=$((checked + 1))
  # decode(), not a cast: a BLOB cast to VARCHAR renders every quote as \x22 and the document
  # then parses as nothing, which reads exactly like a file that carries no footer at all.
  doc=$("$D" -noheader -list -c "SELECT decode(value) FROM parquet_kv_metadata('$f')
        WHERE key::VARCHAR = 'temporal'" 2>/dev/null || true)
  name=$(basename "$dir")
  if [ -z "$doc" ]; then
    echo "  $name: NO temporal footer  ($f)" >&2
    status=1
  elif ! printf '%s' "$doc" | python3 -c "
import json, sys
d = json.loads(sys.stdin.read())
c = d['columns'][d['primary_temporal_column']]
assert d['version'] == '2.0.0', d['version']
for k in ('encoding', 'encoding_version', 'base_type', 'srid', 'edges'):
    assert k in c, k
" 2>/dev/null; then
    echo "  $name: temporal footer is not the document the specification states  ($f)" >&2
    status=1
  else
    echo "  $name: temporal footer"
  fi
done

[ "$checked" -gt 0 ] || { echo "94_check_footer.sh: no parquet under $RUN" >&2; exit 1; }
[ "$status" -eq 0 ] || { echo "94_check_footer.sh: the zone does not describe itself" >&2; exit 1; }
echo "footer: $checked of $checked carry the temporal document"
