/*****************************************************************************
 *
 * cellcover.c
 *
 * Soundness of a discrete-global-grid cell cover used as a prefilter between
 * a catalog's scalar-box pruning and the exact moving-object predicate, over
 * the planar corpus carried to WGS84.
 *
 * A prefilter may only remove a candidate it can prove does not qualify, so
 * the cover has to be conservative: every trip the exact predicate accepts
 * must survive the cell test. This program measures whether it is, over the
 * constructions that the trip side and the region side each admit:
 *
 *   trip side    per-instant   the cells holding the recorded positions
 *                swept         every cell the interpolated path meets
 *   region side  centres       the cells whose centre lies in the region
 *                exact         the cells holding a point of the region, the
 *                              cover geoToH3IndexSet answers
 *                exact + ring  that cover dilated by one grid ring
 *
 * H3 reads geographic coordinates only, so everything here is in WGS84: the
 * trips are the corpus trips transformed to EPSG:4326 instant by instant
 * (46_trips.sh) and the windows the corpus rectangles transformed corner by
 * corner (45_windows.sql). Ground truth is the exact predicate on those
 * objects: the trip, restricted to the window's period, meets the window's
 * polygon. The covers read the same trip and the same polygon. Recall is the
 * share of the trips the exact predicate accepts that also pass the cell test;
 * anything below one is a filter that drops answers.
 *
 * Copyright (c) 2026, Esteban Zimanyi, Universite Libre de Bruxelles
 *
 *****************************************************************************/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <unistd.h>

#include "cellcover_common.h"

/**
 * @brief Score one trip against every window under every pair of covers
 * @param[in] inst The trip's instants, in WGS84
 * @param[out] errors Incremented for each window the exact predicate cannot
 * decide
 */
static void
trip_score(TInstant **inst, int ninst, const Window *w, int nwin,
  int resolution, Tally tally[TRIP_N][REGION_N], int64 *decoded, int64 *errors,
  bool *admits)
{
  Temporal *cover[TRIP_N];
  H3Index *cells[TRIP_N];
  int ncells[TRIP_N];

  /* The trip states a straight line between its instants, which is what the
   * exact predicate reads and what the swept cover walks. The per-instant
   * cover is the same constructor over the same instants saying nothing
   * between them, so it records only the cells the samples fall in. */
  TSequence *trip = tsequence_make(inst, ninst, true, true, LINEAR, true);
  TSequence *discrete = tsequence_make(inst, ninst, true, true, DISCRETE,
    true);
  cover[TRIP_INSTANT] = tgeompoint_to_th3index((Temporal *) discrete,
    resolution);
  cover[TRIP_SWEPT] = tgeompoint_to_th3index((Temporal *) trip, resolution);

  for (int i = 0; i < TRIP_N; i++)
    cells[i] = th3index_cells(cover[i], &ncells[i]);

  /* Whether a cover admits a window depends on the window's polygon alone,
   * so the windows sharing one share the answer: computed once on each
   * representative and read back below. */
  for (int k = 0; k < nwin; k++)
  {
    if (w[k].geom != k)
      continue;
    for (int i = 0; i < TRIP_N; i++)
      for (int j = 0; j < REGION_N; j++)
        admits[(i * REGION_N + j) * nwin + k] =
          cells_intersect(cells[i], ncells[i], w[k].cells[j], w[k].ncells[j]);
  }

  for (int k = 0; k < nwin; k++)
  {
    /* The exact predicate: the trip over the window's period meets its
     * polygon. An answer the predicate cannot give is counted apart, never
     * read as a trip that does not qualify. */
    Temporal *during = temporal_at_tstzspan((const Temporal *) trip,
      w[k].period);
    bool qualifies = false;
    if (during != NULL)
    {
      int meets = eintersects_tgeo_geo(during, w[k].region);
      if (meets < 0)
        (*errors)++;
      qualifies = (meets == 1);
      free(during);
    }
    (*decoded)++;

    for (int i = 0; i < TRIP_N; i++)
      for (int j = 0; j < REGION_N; j++)
      {
        bool ad = admits[(i * REGION_N + j) * nwin + w[k].geom];
        if (ad)
          tally[i][j].admitted++;
        if (qualifies)
        {
          tally[i][j].truth++;
          if (ad)
            tally[i][j].kept++;
        }
      }
  }

  for (int i = 0; i < TRIP_N; i++)
  {
    free(cells[i]);
    free(cover[i]);
  }
  free(discrete);
  free(trip);
}

/**
 * @brief Write the whole result block to a stream
 */
static void
emit_results(FILE *out, int64 ntrips, int64 decoded, int64 errors,
  int resolution, const Window *w, int nwin,
  const Tally tally[TRIP_N][REGION_N])
{
  fprintf(out, "trips,%" PRId64 "\n", ntrips);
  fprintf(out, "pairs,%" PRId64 "\n", decoded);
  fprintf(out, "predicate_errors,%" PRId64 "\n", errors);
  fprintf(out, "resolution,%d\n", resolution);
  fprintf(out, "region_cover,cells\n");
  for (int j = 0; j < REGION_N; j++)
  {
    int64 total = 0;
    for (int k = 0; k < nwin; k++)
      total += w[k].ncells[j];
    fprintf(out, "%s,%" PRId64 "\n", region_name[j], total);
  }
  fprintf(out, "trip_cover,region_cover,truth,kept,false_neg,recall,admitted\n");
  for (int i = 0; i < TRIP_N; i++)
    for (int j = 0; j < REGION_N; j++)
    {
      const Tally *t = &tally[i][j];
      double recall = (t->truth > 0) ? (double) t->kept / (double) t->truth : 1.0;
      fprintf(out, "%s,%s,%" PRId64 ",%" PRId64 ",%" PRId64 ",%.6f,%" PRId64 "\n",
        trip_name[i], region_name[j], t->truth, t->kept, t->truth - t->kept,
        recall, t->admitted);
    }
}

/**
 * @brief Replace the checkpoint file with the current totals
 * @details The block goes to a sibling path and is renamed over the target,
 * so a kill during the write leaves the previous checkpoint whole.
 */
static void
checkpoint_results(const char *path, int64 ntrips, int64 decoded,
  int64 errors, int resolution, const Window *w, int nwin,
  const Tally tally[TRIP_N][REGION_N])
{
  char tmp[PATH_MAX];
  snprintf(tmp, sizeof(tmp), "%s.part", path);
  FILE *out = fopen(tmp, "w");
  if (out == NULL)
    return;
  emit_results(out, ntrips, decoded, errors, resolution, w, nwin, tally);
  fflush(out);
  fsync(fileno(out));
  fclose(out);
  rename(tmp, path);
}

int
main(int argc, char **argv)
{
  if (argc < 4)
  {
    fprintf(stderr, "usage: %s <trips.csv|trips.bin> <windows.psv> <resolution> [checkpoint.csv]\n"
      "  trips.csv    trip_id,lon,lat,unix_seconds  WGS84, ordered by trip_id then time\n"
      "  windows.psv  name|t0_unix|t1_unix|wgs84_polygon_wkt\n", argv[0]);
    return 1;
  }
  const char *trips_path = argv[1];
  const char *windows_path = argv[2];
  int resolution = atoi(argv[3]);
  const char *checkpoint_path = (argc > 4) ? argv[4] : "cellcover.checkpoint.csv";

  meos_initialize();
  meos_initialize_timezone("UTC");

  int nwin;
  Window *w = windows_read(windows_path, resolution, &nwin);
  fprintf(stderr, "windows: %d at resolution %d\n", nwin, resolution);
  for (int k = 0; k < nwin; k++)
    fprintf(stderr, "  %-24s centres=%d exact=%d exact_ring=%d\n", w[k].name,
      w[k].ncells[REGION_CENTRES], w[k].ncells[REGION_EXACT],
      w[k].ncells[REGION_EXACT_RING]);

  Tally tally[TRIP_N][REGION_N];
  memset(tally, 0, sizeof(tally));

  TripSource src;
  if (! trip_source_open(&src, trips_path))
  {
    fprintf(stderr, "cannot open %s\n", trips_path);
    return 1;
  }

  bool *admits = calloc((size_t) TRIP_N * REGION_N * (nwin > 0 ? nwin : 1),
    sizeof(bool));
  TInstant **inst = calloc(MAX_TRIP_POINTS, sizeof(TInstant *));
  int ninst = 0;
  int64 cur_id = -1, ntrips = 0, decoded = 0, errors = 0;

  while (true)
  {
    TripRow r = { 0, 0.0, 0.0, 0 };
    bool parsed = trip_source_next(&src, &r);
    bool eof = ! parsed;

    if ((eof || (parsed && r.id != cur_id)) && ninst >= 2)
    {
      trip_score(inst, ninst, w, nwin, resolution, tally, &decoded, &errors,
        admits);
      ntrips++;
      if (ntrips % 1000 == 0)
      {
        fprintf(stderr, "\r  trips %" PRId64, ntrips);
        checkpoint_results(checkpoint_path, ntrips, decoded, errors,
          resolution, w, nwin, tally);
      }
    }
    if (eof || (parsed && r.id != cur_id))
    {
      for (int i = 0; i < ninst; i++)
        free(inst[i]);
      ninst = 0;
      cur_id = r.id;
    }
    if (eof)
      break;
    if (! parsed || ninst == MAX_TRIP_POINTS)
      continue;

    TimestampTz t = (TimestampTz) ((r.secs - UNIX_TO_PG_EPOCH) * 1000000);
    GSERIALIZED *gs = geompoint_make2d(GEO_SRID, r.lon, r.lat);
    inst[ninst++] = tpointinst_make(gs, t);
    free(gs);
  }
  trip_source_close(&src);
  fprintf(stderr, "\r  trips %" PRId64 "\n", ntrips);

  emit_results(stdout, ntrips, decoded, errors, resolution, w, nwin, tally);
  remove(checkpoint_path);
  windows_free(w, nwin);
  free(admits);
  free(inst);
  meos_finalize();
  return 0;
}
