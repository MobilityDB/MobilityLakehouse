#!/usr/bin/env bash
# Build the planar cell-cover harness against ONE named MEOS prefix, built from a MobilityDB commit
# whose geoToH3IndexSet answers the exact cover, the cells holding a point of a geometry (the
# harness refuses at run time a MEOS whose cover of a region holds more), and against the H3
# release MEOS itself is built with: the harness computes the region covers with
# libh3 (polygonToCells, gridDisk) while MEOS computes each trip's cover with its own static copy,
# so both sides of every comparison come from one H3. The H3 archive is linked by path, since
# `-lh3` takes the first libh3.so on the linker's search path, which is a distribution's H3 of
# another release. The compile flags are the prefix's own, from its meos.pc.
#
# ISOLATION IS PROVEN, NOT ASSERTED: `-I<dir>` only prepends a search path, and a stale
# /usr/local/include/meos.h or a distribution's /usr/include/h3api.h would satisfy an include just
# as well, so every meos header the preprocessor resolves is checked to sit inside the prefix and
# h3api.h inside the H3 include directory before anything compiles; the linked binaries are then
# checked to load no shared libh3.
#
#   ./build.sh <prefix>
#
# Environment: H3_INCLUDE_DIR (/usr/local/include/h3) and H3_LIBRARY (/usr/local/lib/libh3.a),
# the values of the same names in the MEOS build's CMakeCache.txt.
set -euo pipefail

D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=${1:?prefix is required}
H3_INCLUDE_DIR=${H3_INCLUDE_DIR:-/usr/local/include/h3}
H3_LIBRARY=${H3_LIBRARY:-/usr/local/lib/libh3.a}
CFLAGS="-O2 -Wall -Wextra"

[ -f "$PREFIX/include/meos.h" ] || { echo "no meos.h under $PREFIX" >&2; exit 1; }
[ -f "$PREFIX/lib/libmeos.so" ] || { echo "no libmeos.so under $PREFIX" >&2; exit 1; }
[ -f "$PREFIX/lib/pkgconfig/meos.pc" ] || { echo "no meos.pc under $PREFIX" >&2; exit 1; }
[ -f "$H3_INCLUDE_DIR/h3api.h" ] || { echo "no h3api.h under $H3_INCLUDE_DIR" >&2; exit 1; }
[ -f "$H3_LIBRARY" ] || { echo "no H3 archive at $H3_LIBRARY" >&2; exit 1; }
MEOS_CFLAGS=$(PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" pkg-config --cflags meos)
H3_VERSION=$(grep -E '#define H3_VERSION_(MAJOR|MINOR|PATCH) ' "$H3_INCLUDE_DIR/h3api.h" \
  | awk '{print $3}' | paste -sd.)
echo ">> prefix: $PREFIX"
echo ">> libmeos: $(stat -c '%s B  %y' "$PREFIX/lib/libmeos.so")"
echo ">> H3 $H3_VERSION: $H3_INCLUDE_DIR, $H3_LIBRARY"

stray=0
for src in cellcover_common.c cellcover.c coarsen.c cellcost.c trips_pack.c; do
  while read -r h; do
    case "$h" in
      "$PREFIX"/*/meos*.h | "$H3_INCLUDE_DIR"/h3api.h) ;;
      *) echo "   STRAY  $src -> $h"; stray=1 ;;
    esac
  done < <(gcc -M $MEOS_CFLAGS -I"$H3_INCLUDE_DIR" "$D/$src" 2>/dev/null \
             | tr ' ' '\n' | grep -E '/(meos[a-z_]*|h3api)\.h$' | sort -u)
done
[ "$stray" -eq 0 ] || { echo ">> refusing to build: headers resolve outside $PREFIX and $H3_INCLUDE_DIR" >&2; exit 1; }
echo "   every meos header resolves inside the prefix, h3api.h inside $H3_INCLUDE_DIR"

build() {
  local out=$1; shift
  echo ">> $out"
  gcc $CFLAGS $MEOS_CFLAGS -I"$H3_INCLUDE_DIR" -o "$D/$out" "$@" \
    -L"$PREFIX/lib" -Wl,-rpath,"$PREFIX/lib" -lmeos "$H3_LIBRARY" -lm
  if ldd "$D/$out" | grep -q libh3; then
    echo ">> $out loads a shared libh3: $(ldd "$D/$out" | grep libh3)" >&2
    exit 1
  fi
}
build cellcover  "$D/cellcover.c"  "$D/cellcover_common.c"
build coarsen    "$D/coarsen.c"    "$D/cellcover_common.c"
build cellcost   "$D/cellcost.c"   "$D/cellcover_common.c"
build trips_pack "$D/trips_pack.c" "$D/cellcover_common.c"
ldd "$D/cellcover" | grep -E "libmeos"
