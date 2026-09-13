/*****************************************************************************
 *
 * trips_pack.c
 *
 * Write the positions of a text trip file to the packed form the planar
 * cell-cover experiments read: the same positions, in the same order, as the
 * records a reader takes without parsing, so every resolution after the first
 * reads them without paying for the text again.
 *
 * Copyright (c) 2026, Esteban Zimanyi, Universite Libre de Bruxelles
 *
 *****************************************************************************/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cellcover_common.h"

int
main(int argc, char **argv)
{
  if (argc < 3)
  {
    fprintf(stderr, "usage: %s <trips.csv> <trips.bin>\n"
      "  trips.csv  trip_id,lon,lat,unix_seconds  WGS84, ordered by trip_id then time\n"
      "  trips.bin  the same positions, as records\n", argv[0]);
    return 1;
  }
  int64 nrows = 0;
  if (! trips_csv_to_bin(argv[1], argv[2], &nrows))
  {
    fprintf(stderr, "cannot read %s or write %s\n", argv[1], argv[2]);
    return 1;
  }
  fprintf(stderr, "%s: %" PRId64 " positions\n", argv[2], nrows);
  return 0;
}
