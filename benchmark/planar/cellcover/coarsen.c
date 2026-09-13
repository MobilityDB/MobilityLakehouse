/*****************************************************************************
 *
 * coarsen.c
 *
 * Whether a stored cell path can be coarsened in place, and what that costs
 * against rebuilding it from the trajectory, over the planar corpus carried
 * to WGS84.
 *
 * A cover stored at one resolution is a column, and a question asked at a
 * coarser grain can either read that column through `cellToParent` or build a
 * fresh cover from the trajectory. The two are interchangeable only if the
 * coarsened path admits every candidate the rebuilt one admits: a prefilter
 * may only remove what it can prove does not qualify, so a coarsening that
 * loses a cell loses answers.
 *
 * This program measures both halves over the trips and windows cellcover.c
 * reads (46_trips.sh, 45_windows.sql):
 *
 *   soundness   whether the rebuilt cover is contained in the coarsened one,
 *               counted in cells and in trips that differ
 *   cost        the time to coarsen a stored path against the time to build
 *               the same grain from the trajectory
 *
 * Both paths are swept covers, each tested against the windows' ring covers
 * at the coarse resolution. Ground truth is the exact predicate of
 * cellcover.c, the trip over the window's period meeting its polygon, so the
 * admitted counts of the two constructions are reported against the same
 * denominator the soundness sweep uses.
 *
 * Copyright (c) 2026, Esteban Zimanyi, Universite Libre de Bruxelles
 *
 *****************************************************************************/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <limits.h>
#include <unistd.h>

#include "cellcover_common.h"

/** The two ways to reach a cover at the coarse resolution */
typedef enum { PATH_COARSENED, PATH_REBUILT, PATH_N } coarsePath;

static const char *path_name[PATH_N] = { "coarsened", "rebuilt" };

/** What the run measured, accumulated over every trip */
typedef struct
{
  int64  ncells[PATH_N];      /**< cells summed over the trips */
  int64  admitted[PATH_N];    /**< window pairs the cover admits */
  int64  kept[PATH_N];        /**< of the qualifying pairs, those it admits */
  int64  truth;               /**< pairs the exact predicate accepts */
  int64  errors;              /**< pairs the exact predicate cannot decide */
  int64  stored_cells;        /**< cells of the stored path, before coarsening */
  int64  missing_cells;       /**< cells the rebuilt cover holds and the
                                *  coarsened one does not */
  int64  trips_missing;       /**< trips carrying at least one such cell */
  double coarsen_seconds;     /**< time reading the stored path down */
  double rebuild_seconds;     /**< time building the coarse path from the trip */
} Measure;

static double
now_seconds(void)
{
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (double) t.tv_sec + (double) t.tv_nsec / 1e9;
}

/**
 * @brief Return the cells of a stored path read down to a coarser resolution
 * @details Every cell of the stored path has exactly one ancestor at the
 * coarser resolution, so reading the path down is a map followed by the
 * deduplication every cover carries. The trajectory is never revisited.
 */
static H3Index *
cells_coarsen(const H3Index *in, int nin, int coarse_res, int *count)
{
  H3Index *out = calloc((size_t) (nin > 0 ? nin : 1), sizeof(H3Index));
  int n = 0;
  for (int i = 0; i < nin; i++)
  {
    H3Index parent;
    if (cellToParent(in[i], coarse_res, &parent) == E_SUCCESS)
      out[n++] = parent;
  }
  *count = cells_sort_uniq(out, n);
  return out;
}

/**
 * @brief Return how many cells of @p b are absent from @p a
 * @details Both arrays are sorted and duplicate-free, so one merge answers
 * it. A non-zero count is a cell the rebuilt cover reaches and the coarsened
 * one does not, which is a candidate the coarsened path would drop.
 */
static int
cells_missing(const H3Index *a, int na, const H3Index *b, int nb)
{
  int i = 0, j = 0, missing = 0;
  while (j < nb)
  {
    if (i >= na)
    {
      missing += nb - j;
      break;
    }
    if (a[i] == b[j])
    {
      i++; j++;
    }
    else if (a[i] < b[j])
      i++;
    else
    {
      missing++;
      j++;
    }
  }
  return missing;
}

/**
 * @brief Measure one trip under both ways of reaching the coarse cover
 */
static void
trip_measure(TInstant **instants, int ninst, const Window *w, int nwin,
  int stored_res, int coarse_res, Measure *m, bool *admits)
{
  TSequence *linear = tsequence_make(instants, ninst, true, true, LINEAR,
    true);
  const Temporal *trip = (const Temporal *) linear;

  /* The stored path: the column the deployment already holds */
  Temporal *stored = tgeompoint_to_th3index(trip, stored_res);
  int nstored;
  H3Index *stored_cells = th3index_cells(stored, &nstored);
  m->stored_cells += nstored;

  H3Index *cells[PATH_N];
  int ncells[PATH_N];

  double t0 = now_seconds();
  cells[PATH_COARSENED] = cells_coarsen(stored_cells, nstored, coarse_res,
    &ncells[PATH_COARSENED]);
  double t1 = now_seconds();
  m->coarsen_seconds += t1 - t0;

  t0 = now_seconds();
  Temporal *rebuilt = tgeompoint_to_th3index(trip, coarse_res);
  cells[PATH_REBUILT] = th3index_cells(rebuilt, &ncells[PATH_REBUILT]);
  t1 = now_seconds();
  m->rebuild_seconds += t1 - t0;

  int missing = cells_missing(cells[PATH_COARSENED], ncells[PATH_COARSENED],
    cells[PATH_REBUILT], ncells[PATH_REBUILT]);
  m->missing_cells += missing;
  if (missing > 0)
    m->trips_missing++;
  for (int p = 0; p < PATH_N; p++)
    m->ncells[p] += ncells[p];

  /* A cover admits a window on the window's polygon alone, so the windows
   * sharing a polygon share the answer */
  for (int k = 0; k < nwin; k++)
  {
    if (w[k].geom != k)
      continue;
    for (int p = 0; p < PATH_N; p++)
      admits[p * nwin + k] = cells_intersect(cells[p], ncells[p],
        w[k].cells[REGION_RING], w[k].ncells[REGION_RING]);
  }

  for (int k = 0; k < nwin; k++)
  {
    /* The exact predicate: the trip over the window's period meets its
     * polygon. An answer the predicate cannot give is counted apart, never
     * read as a trip that does not qualify. */
    Temporal *during = temporal_at_tstzspan(trip, w[k].period);
    bool qualifies = false;
    if (during != NULL)
    {
      int meets = eintersects_tgeo_geo(during, w[k].region);
      if (meets < 0)
        m->errors++;
      qualifies = (meets == 1);
      free(during);
    }
    if (qualifies)
      m->truth++;
    for (int p = 0; p < PATH_N; p++)
    {
      bool ad = admits[p * nwin + w[k].geom];
      if (ad)
        m->admitted[p]++;
      if (qualifies && ad)
        m->kept[p]++;
    }
  }

  for (int p = 0; p < PATH_N; p++)
    free(cells[p]);
  free(stored_cells);
  free(stored);
  free(rebuilt);
  free(linear);
}

/**
 * @brief Write the whole result block to a stream
 */
static void
emit_results(FILE *out, int64 ntrips, int stored_res, int coarse_res,
  const Measure *m)
{
  fprintf(out, "trips,%" PRId64 "\n", ntrips);
  fprintf(out, "stored_res,%d\n", stored_res);
  fprintf(out, "coarse_res,%d\n", coarse_res);
  fprintf(out, "predicate_errors,%" PRId64 "\n", m->errors);
  fprintf(out, "stored_cells,%" PRId64 "\n", m->stored_cells);
  fprintf(out, "coarsen_seconds,%.3f\n", m->coarsen_seconds);
  fprintf(out, "rebuild_seconds,%.3f\n", m->rebuild_seconds);
  fprintf(out, "coarsen_over_rebuild,%.6f\n",
    (m->rebuild_seconds > 0.0) ? m->coarsen_seconds / m->rebuild_seconds : 0.0);
  fprintf(out, "missing_cells,%" PRId64 "\n", m->missing_cells);
  fprintf(out, "trips_missing,%" PRId64 "\n", m->trips_missing);
  fprintf(out, "path,cells,candidates,truth,kept,recall\n");
  for (int p = 0; p < PATH_N; p++)
  {
    double recall = (m->truth > 0) ? (double) m->kept[p] / (double) m->truth
      : 1.0;
    fprintf(out, "%s,%" PRId64 ",%" PRId64 ",%" PRId64 ",%" PRId64 ",%.6f\n",
      path_name[p], m->ncells[p], m->admitted[p], m->truth, m->kept[p],
      recall);
  }
}

/**
 * @brief Replace the checkpoint file with the current totals
 * @details The block goes to a sibling path and is renamed over the target,
 * so a kill during the write leaves the previous checkpoint whole rather
 * than a truncated one.
 */
static void
checkpoint_results(const char *path, int64 ntrips, int stored_res,
  int coarse_res, const Measure *m)
{
  char tmp[PATH_MAX];
  snprintf(tmp, sizeof(tmp), "%s.part", path);
  FILE *out = fopen(tmp, "w");
  if (out == NULL)
    return;
  emit_results(out, ntrips, stored_res, coarse_res, m);
  fflush(out);
  fsync(fileno(out));
  fclose(out);
  rename(tmp, path);
}

int
main(int argc, char **argv)
{
  if (argc < 5)
  {
    fprintf(stderr, "usage: %s <trips.csv|trips.bin> <windows.psv> <stored_res> "
      "<coarse_res> [checkpoint.csv]\n"
      "  trips.csv    trip_id,lon,lat,unix_seconds  WGS84, ordered by trip_id then time\n"
      "  windows.psv  name|t0_unix|t1_unix|wgs84_polygon_wkt\n", argv[0]);
    return 1;
  }
  const char *trips_path = argv[1];
  const char *windows_path = argv[2];
  int stored_res = atoi(argv[3]);
  int coarse_res = atoi(argv[4]);
  const char *checkpoint_path = (argc > 5) ? argv[5]
    : "coarsen.checkpoint.csv";

  if (coarse_res >= stored_res)
  {
    fprintf(stderr, "coarse_res %d must be coarser than the stored one: a stored path "
      "reads DOWN to a coarser grain, so coarse_res < stored_res %d\n",
      coarse_res, stored_res);
    return 1;
  }

  meos_initialize();
  meos_initialize_timezone("UTC");

  /* The candidates are counted against the covers at the grain the question
   * is asked at, which is the coarse one */
  int nwin;
  Window *w = windows_read(windows_path, coarse_res, &nwin);
  fprintf(stderr, "windows: %d, stored res %d read down to %d\n", nwin,
    stored_res, coarse_res);

  Measure m;
  memset(&m, 0, sizeof(m));
  bool *admits = calloc((size_t) (PATH_N * (nwin > 0 ? nwin : 1)), sizeof(bool));

  TripSource src;
  if (! trip_source_open(&src, trips_path))
  {
    fprintf(stderr, "cannot open %s\n", trips_path);
    return 1;
  }

  TInstant **instants = calloc(MAX_TRIP_POINTS, sizeof(TInstant *));
  int ninst = 0;
  int64 cur_id = -1, ntrips = 0;

  while (true)
  {
    TripRow r = { 0, 0.0, 0.0, 0 };
    bool parsed = trip_source_next(&src, &r);
    bool eof = ! parsed;

    if ((eof || (parsed && r.id != cur_id)) && ninst >= 2)
    {
      trip_measure(instants, ninst, w, nwin, stored_res, coarse_res, &m,
        admits);
      ntrips++;
      if (ntrips % 1000 == 0)
      {
        fprintf(stderr, "\r  trips %" PRId64, ntrips);
        checkpoint_results(checkpoint_path, ntrips, stored_res, coarse_res,
          &m);
      }
    }
    if (eof || (parsed && r.id != cur_id))
    {
      for (int i = 0; i < ninst; i++)
        free(instants[i]);
      ninst = 0;
      cur_id = r.id;
    }
    if (eof)
      break;
    if (! parsed || ninst == MAX_TRIP_POINTS)
      continue;

    TimestampTz t = (TimestampTz) ((r.secs - UNIX_TO_PG_EPOCH) * 1000000);
    GSERIALIZED *gs = geompoint_make2d(GEO_SRID, r.lon, r.lat);
    instants[ninst++] = tpointinst_make(gs, t);
    free(gs);
  }
  trip_source_close(&src);
  fprintf(stderr, "\r  trips %" PRId64 "\n", ntrips);

  emit_results(stdout, ntrips, stored_res, coarse_res, &m);
  remove(checkpoint_path);
  windows_free(w, nwin);
  free(admits);
  free(instants);
  meos_finalize();
  return 0;
}
