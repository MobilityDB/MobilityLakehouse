/*****************************************************************************
 *
 * coarsen.c
 *
 * Whether a stored cell path can be coarsened in place, and what that costs
 * against rebuilding it from the trajectory, over the planar corpus carried
 * to WGS84.
 *
 * A cover stored at one resolution is a column, and a question asked at a
 * coarser grain can either read that column down or build a fresh cover from
 * the trajectory. The two are interchangeable only if the path read down
 * admits every candidate the rebuilt one admits: a prefilter may only remove
 * what it can prove does not qualify, so a coarsening that loses a cell loses
 * answers.
 *
 * This program reads the stored path down in three ways, each from the stored
 * cells alone and never from the trajectory, and compares each with the cover
 * rebuilt from the trajectory at the coarse resolution, over the trips and
 * windows cellcover.c reads (46_trips.sh, 45_windows.sql):
 *
 *   coarsened        the parent of every stored cell, `cellToParent`
 *   neighbour parents the parents of every stored cell and of its six
 *                    `gridDisk` neighbours, taken two resolutions at a time,
 *                    which holds every cell the stored one reaches and costs
 *                    the index arithmetic alone
 *   parent + 1 ring  every such parent with its six neighbours, `gridDisk` of
 *                    radius one around it
 *   hexagon cover    the union, over the stored cells, of the exact coarse
 *                    cover of each cell, `h3index_cell_to_cover`; the path
 *                    lies in the union of its stored cells, so each of its
 *                    points lies in a cell of this cover. That entry walks
 *                    the cell's edges as the arcs of great circles they are,
 *                    which is the cell itself: a cell read as a polygon of
 *                    its vertices is read as a PLANAR one, whose edges are
 *                    straight lines in longitude and latitude and so describe
 *                    another figure. The cover of each distinct stored cell
 *                    is computed once over the run and kept, up to a bounded
 *                    table.
 *
 * For each path it reports the cells the rebuilt cover holds and the path
 * lacks, counted in cells and in trips, the time to build it, and, per window
 * and over all of them, the candidates it admits and the recall against the
 * windows' exact covers, the cells holding a point of the region, and against
 * those covers dilated by one grid ring. It also reports how far each rebuilt
 * cell the coarsened path lacks lies from that path, in grid rings. Ground
 * truth is the exact predicate of cellcover.c, the trip over the window's
 * period meeting its polygon.
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

/** The ways to reach a cover at the coarse resolution, in the order they are reported */
typedef enum { PATH_COARSENED, PATH_NEIGHBOUR, PATH_PARENT_RING, PATH_HEXAGON,
  PATH_REBUILT, PATH_N } coarsePath;

static const char *path_name[PATH_N] = { "coarsened", "neighbour parents",
  "parent + 1 ring", "hexagon cover", "rebuilt" };

/** The region covers each path is tested against, in the order they are reported */
static const regionCover coarse_region[] = { REGION_EXACT, REGION_EXACT_RING };
#define COARSE_REGION_N  2

/** The largest ring distance counted apart; farther cells share one bucket */
#define RING_MAX  8

/** Initial slots of the hexagon cover table, a power of two */
#define HEX_TABLE_INIT   (1 << 20)
/** Slots the hexagon cover table grows to at most, a power of two: 134M
 * slots of 24 bytes hold 67M stored cells in 3.2 GB, and a stored cell met
 * once the table is full has its cover computed each time it is met */
#define HEX_TABLE_MAX    (1 << 27)
/** Rebuilt cells the hexagon cover lacks that are named on stderr */
#define HEX_MISSING_NAMED  50

/** What the run measured over one window */
typedef struct
{
  int64  admitted[PATH_N][COARSE_REGION_N];  /**< trips the path admits */
  int64  kept[PATH_N][COARSE_REGION_N];      /**< of the qualifying trips, those it admits */
  int64  truth;                              /**< trips the exact predicate accepts */
} WindowMeasure;

/** What the run measured, accumulated over every trip */
typedef struct
{
  int64  ncells[PATH_N];         /**< cells summed over the trips */
  double seconds[PATH_N];        /**< time building each path */
  int64  missing_cells[PATH_N];  /**< cells the rebuilt cover holds and the path lacks */
  int64  trips_missing[PATH_N];  /**< trips carrying at least one such cell */
  int64  extra_cells[PATH_N];    /**< cells the path holds and the rebuilt cover lacks */
  int64  ring[RING_MAX + 2];     /**< rebuilt cells absent from the coarsened path,
                                   *  by their ring distance to it, RING_MAX + 1
                                   *  counting the farther ones */
  int64  errors;                 /**< pairs the exact predicate cannot decide */
  int64  stored_cells;           /**< cells of the stored paths */
  int64  hex_hits, hex_misses;   /**< stored cells whose hexagon cover was
                                   *  kept already, and computed */
  int64  hex_uncached;           /**< of those computed, the ones the full
                                   *  table could not keep */
} Measure;

/** One slot of the hexagon cover table */
typedef struct
{
  H3Index key;       /**< the stored cell, 0 when empty */
  int64   first;     /**< the first cell of its cover in hex_cells */
  int32   n;         /**< the cells of its cover */
} HexSlot;

/** The hexagon cover of every distinct stored cell met so far: an open
 * addressing table of slots over one array of cover cells */
static HexSlot *hex_slots;
static int64 hex_nslots, hex_used;
static H3Index *hex_cells;
static int64 hex_ncells, hex_capcells;

static double
now_seconds(void)
{
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (double) t.tv_sec + (double) t.tv_nsec / 1e9;
}

/**
 * @brief Return the parents of each stored cell and of its neighbours
 * @details A cell reaches, at a resolution one or two levels coarser, no cell
 * the parents of its `gridDisk(cell, 1)` neighbours do not already state; the
 * probe `~/cpwork/h3-cellcover/table.c` reads 0 cells missing against the
 * exact cover over 300,000 cells drawn over the whole Earth and over every
 * cell within four rings of each of the twelve pentagons, and 3,473 missing
 * at a gap of three, so a wider gap is taken two levels at a time. The cover
 * so built holds cells the exact one does not, which is what a conservative
 * cover is free to do, and it costs the index arithmetic alone.
 */
static H3Index *
cells_neighbour_parents(const H3Index *in, int nin, int stored_res,
  int coarse_res, int *count)
{
  int cap = (nin > 0 ? nin : 1) * 7;
  H3Index *cur = calloc((size_t) cap, sizeof(H3Index));
  int ncur = 0;
  for (int i = 0; i < nin; i++)
    cur[ncur++] = in[i];
  int res = stored_res;
  while (res > coarse_res)
  {
    int target = res - ((res - coarse_res >= 2) ? 2 : 1);
    int ocap = ncur * 7 + 1;
    H3Index *out = calloc((size_t) ocap, sizeof(H3Index));
    int n = 0;
    for (int i = 0; i < ncur; i++)
    {
      H3Index disk[7];
      if (gridDisk(cur[i], 1, disk) != E_SUCCESS)
        continue;
      for (int k = 0; k < 7; k++)
      {
        H3Index parent;
        if (disk[k] == 0 ||
            cellToParent(disk[k], target, &parent) != E_SUCCESS)
          continue;
        out[n++] = parent;
      }
    }
    n = cells_sort_uniq(out, n);
    free(cur);
    cur = out;
    ncur = n;
    res = target;
  }
  *count = ncur;
  return cur;
}

/**
 * @brief Return the cells of a stored path read down to a coarser resolution
 * @details Every cell of the stored path has exactly one ancestor at the
 * coarser resolution, so reading the path down is a map followed by the
 * deduplication every cover carries.
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
 * @brief Return the exact coarse cover of one stored cell
 * @details `h3index_cell_to_cover` states it: the walk of the cell's edges,
 * which are arcs of great circles, at the coarse resolution. That walk is the
 * whole cover there, since a cell of a resolution no finer than this one is at
 * least as wide and cannot meet it without holding a point of its boundary.
 * The returned array belongs to the caller.
 */
static H3Index *
cell_hexagon_cover(H3Index cell, int coarse_res, int *count)
{
  *count = 0;
  Set *s = h3index_cell_to_cover(cell, coarse_res);
  if (s == NULL)
    return NULL;
  H3Index *cells = h3indexset_values(s, count);
  free(s);
  return cells;
}

static uint64_t
cell_hash(H3Index cell)
{
  uint64_t z = (uint64_t) cell + 0x9e3779b97f4a7c15ULL;
  z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
  z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
  return z ^ (z >> 31);
}

/**
 * @brief Return the slot holding a stored cell, or the empty slot where it
 * belongs
 */
static HexSlot *
hex_lookup(H3Index cell)
{
  int64 i = (int64) (cell_hash(cell) & (uint64_t) (hex_nslots - 1));
  while (hex_slots[i].key != (H3Index) 0 && hex_slots[i].key != cell)
    i = (i + 1) & (hex_nslots - 1);
  return &hex_slots[i];
}

/**
 * @brief Keep the hexagon cover of a stored cell, doubling the table when it
 * is half full, and return its slot, or NULL when the table is full
 */
static HexSlot *
hex_insert(H3Index cell, const H3Index *cells, int n)
{
  if (2 * (hex_used + 1) > hex_nslots && hex_nslots >= HEX_TABLE_MAX)
    return NULL;
  if (2 * (hex_used + 1) > hex_nslots)
  {
    HexSlot *old = hex_slots;
    int64 nold = hex_nslots;
    hex_nslots *= 2;
    hex_slots = calloc((size_t) hex_nslots, sizeof(HexSlot));
    if (hex_slots == NULL)
    {
      fprintf(stderr, "cannot grow the hexagon cover table to %" PRId64
        " slots\n", hex_nslots);
      exit(1);
    }
    for (int64 i = 0; i < nold; i++)
      if (old[i].key != (H3Index) 0)
        *hex_lookup(old[i].key) = old[i];
    free(old);
  }
  if (hex_ncells + n > hex_capcells)
  {
    hex_capcells = (hex_ncells + n) * 2;
    hex_cells = realloc(hex_cells, (size_t) hex_capcells * sizeof(H3Index));
    if (hex_cells == NULL)
    {
      fprintf(stderr, "cannot grow the hexagon cover cells to %" PRId64 "\n",
        hex_capcells);
      exit(1);
    }
  }
  HexSlot *e = hex_lookup(cell);
  e->key = cell;
  e->first = hex_ncells;
  e->n = n;
  memcpy(hex_cells + hex_ncells, cells, (size_t) n * sizeof(H3Index));
  hex_ncells += n;
  hex_used++;
  return e;
}

/**
 * @brief Return the union of the hexagon covers of the stored cells
 */
static H3Index *
cells_hexagon_cover(const H3Index *in, int nin, int coarse_res, int *count,
  Measure *m)
{
  int cap = (nin > 0 ? nin : 1) * 3, n = 0;
  H3Index *out = malloc((size_t) cap * sizeof(H3Index));
  for (int i = 0; i < nin; i++)
  {
    HexSlot *e = hex_lookup(in[i]);
    H3Index *computed = NULL;
    const H3Index *cells;
    int nc;
    if (e->key == in[i])
    {
      m->hex_hits++;
      cells = hex_cells + e->first;
      nc = e->n;
    }
    else
    {
      m->hex_misses++;
      computed = cell_hexagon_cover(in[i], coarse_res, &nc);
      if (computed == NULL)
      {
        fprintf(stderr, "no hexagon cover for stored cell %" PRIx64 "\n",
          (uint64_t) in[i]);
        exit(1);
      }
      if (hex_insert(in[i], computed, nc) == NULL)
        m->hex_uncached++;
      cells = computed;
    }
    if (n + nc > cap)
    {
      cap = (n + nc) * 2;
      out = realloc(out, (size_t) cap * sizeof(H3Index));
    }
    memcpy(out + n, cells, (size_t) nc * sizeof(H3Index));
    n += nc;
    free(computed);
  }
  *count = cells_sort_uniq(out, n);
  return out;
}

/**
 * @brief Return how many cells of @p b are absent from @p a
 * @details Both arrays are sorted and duplicate-free, so one merge answers
 * it. When @p absent is given, the absent cells are written to it.
 */
static int
cells_missing(const H3Index *a, int na, const H3Index *b, int nb,
  H3Index *absent)
{
  int i = 0, j = 0, missing = 0;
  while (j < nb)
  {
    if (i < na && a[i] == b[j])
    {
      i++; j++;
    }
    else if (i < na && a[i] < b[j])
      i++;
    else
    {
      if (absent != NULL)
        absent[missing] = b[j];
      missing++;
      j++;
    }
  }
  return missing;
}

/**
 * @brief Return the ring distance from a cell to the nearest cell of a sorted
 * array, or RING_MAX + 1 when it is farther than RING_MAX
 */
static int
ring_distance(H3Index cell, const H3Index *cells, int ncells)
{
  for (int k = 1; k <= RING_MAX; k++)
  {
    int64_t size;
    if (maxGridDiskSize(k, &size) != E_SUCCESS)
      break;
    H3Index *disk = calloc((size_t) size, sizeof(H3Index));
    bool found = false;
    if (gridDisk(cell, k, disk) == E_SUCCESS)
    {
      int n = 0;
      for (int64_t i = 0; i < size; i++)
        if (disk[i] != (H3Index) 0)
          disk[n++] = disk[i];
      n = cells_sort_uniq(disk, n);
      found = cells_intersect(disk, n, cells, ncells);
    }
    free(disk);
    if (found)
      return k;
  }
  return RING_MAX + 1;
}

/**
 * @brief Measure one trip under every way of reaching the coarse cover
 */
static void
trip_measure(int64 trip_id, TInstant **instants, int ninst, const Window *w,
  int nwin, int stored_res, int coarse_res, Measure *m, WindowMeasure *wm,
  bool *admits)
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
  m->seconds[PATH_COARSENED] += t1 - t0;

  t0 = now_seconds();
  cells[PATH_NEIGHBOUR] = cells_neighbour_parents(stored_cells, nstored,
    stored_res, coarse_res, &ncells[PATH_NEIGHBOUR]);
  t1 = now_seconds();
  m->seconds[PATH_NEIGHBOUR] += t1 - t0;

  t0 = now_seconds();
  int nparent;
  H3Index *parent = cells_coarsen(stored_cells, nstored, coarse_res, &nparent);
  cells[PATH_PARENT_RING] = cells_dilate(parent, nparent,
    &ncells[PATH_PARENT_RING]);
  t1 = now_seconds();
  m->seconds[PATH_PARENT_RING] += t1 - t0;
  free(parent);

  t0 = now_seconds();
  cells[PATH_HEXAGON] = cells_hexagon_cover(stored_cells, nstored, coarse_res,
    &ncells[PATH_HEXAGON], m);
  t1 = now_seconds();
  m->seconds[PATH_HEXAGON] += t1 - t0;

  t0 = now_seconds();
  Temporal *rebuilt = tgeompoint_to_th3index(trip, coarse_res);
  cells[PATH_REBUILT] = th3index_cells(rebuilt, &ncells[PATH_REBUILT]);
  t1 = now_seconds();
  m->seconds[PATH_REBUILT] += t1 - t0;

  H3Index *absent = malloc((size_t) (ncells[PATH_REBUILT] > 0 ?
    ncells[PATH_REBUILT] : 1) * sizeof(H3Index));
  for (int p = 0; p < PATH_N; p++)
  {
    m->ncells[p] += ncells[p];
    if (p == PATH_REBUILT)
      continue;
    int missing = cells_missing(cells[p], ncells[p], cells[PATH_REBUILT],
      ncells[PATH_REBUILT], absent);
    m->missing_cells[p] += missing;
    if (missing > 0)
      m->trips_missing[p]++;
    m->extra_cells[p] += cells_missing(cells[PATH_REBUILT],
      ncells[PATH_REBUILT], cells[p], ncells[p], NULL);
    if (p == PATH_COARSENED)
      for (int i = 0; i < missing; i++)
        m->ring[ring_distance(absent[i], cells[p], ncells[p])]++;
    if (p == PATH_HEXAGON)
      for (int i = 0; i < missing; i++)
        if (m->missing_cells[p] - missing + i < HEX_MISSING_NAMED)
          fprintf(stderr, "hexagon cover lacks rebuilt cell %" PRIx64
            " of trip %" PRId64 " (%d stored cells)\n", (uint64_t) absent[i],
            trip_id, nstored);
  }
  free(absent);

  /* A cover admits a window on the window's polygon alone, so the windows
   * sharing a polygon share the answer */
  for (int k = 0; k < nwin; k++)
  {
    if (w[k].geom != k)
      continue;
    for (int p = 0; p < PATH_N; p++)
      for (int j = 0; j < COARSE_REGION_N; j++)
        admits[(p * COARSE_REGION_N + j) * nwin + k] = cells_intersect(
          cells[p], ncells[p], w[k].cells[coarse_region[j]],
          w[k].ncells[coarse_region[j]]);
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
      wm[k].truth++;
    for (int p = 0; p < PATH_N; p++)
      for (int j = 0; j < COARSE_REGION_N; j++)
      {
        bool ad = admits[(p * COARSE_REGION_N + j) * nwin + w[k].geom];
        if (ad)
          wm[k].admitted[p][j]++;
        if (qualifies && ad)
          wm[k].kept[p][j]++;
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
  const Window *w, int nwin, const Measure *m, const WindowMeasure *wm)
{
  fprintf(out, "trips,%" PRId64 "\n", ntrips);
  fprintf(out, "stored_res,%d\n", stored_res);
  fprintf(out, "coarse_res,%d\n", coarse_res);
  fprintf(out, "predicate_errors,%" PRId64 "\n", m->errors);
  fprintf(out, "stored_cells,%" PRId64 "\n", m->stored_cells);
  fprintf(out, "hexagon_computed_covers,%" PRId64 "\n", m->hex_misses);
  fprintf(out, "hexagon_kept_covers_read,%" PRId64 "\n", m->hex_hits);
  fprintf(out, "hexagon_covers_not_kept,%" PRId64 "\n", m->hex_uncached);
  fprintf(out, "path,cells,seconds,over_rebuild,missing_cells,trips_missing,"
    "extra_cells\n");
  for (int p = 0; p < PATH_N; p++)
    fprintf(out, "%s,%" PRId64 ",%.3f,%.6f,%" PRId64 ",%" PRId64 ",%" PRId64
      "\n", path_name[p], m->ncells[p], m->seconds[p],
      (m->seconds[PATH_REBUILT] > 0.0) ?
        m->seconds[p] / m->seconds[PATH_REBUILT] : 0.0,
      m->missing_cells[p], m->trips_missing[p], m->extra_cells[p]);
  fprintf(out, "ring_distance_to_coarsened,rebuilt_cells\n");
  for (int k = 1; k <= RING_MAX + 1; k++)
    fprintf(out, "%s%d,%" PRId64 "\n", (k > RING_MAX) ? ">" : "",
      (k > RING_MAX) ? RING_MAX : k, m->ring[k]);
  fprintf(out, "path,region_cover,window,candidates,truth,kept,recall\n");
  for (int p = 0; p < PATH_N; p++)
    for (int j = 0; j < COARSE_REGION_N; j++)
    {
      int64 all_admitted = 0, all_kept = 0, all_truth = 0;
      for (int k = 0; k <= nwin; k++)
      {
        int64 admitted, kept, truth;
        if (k < nwin)
        {
          admitted = wm[k].admitted[p][j];
          kept = wm[k].kept[p][j];
          truth = wm[k].truth;
          all_admitted += admitted;
          all_kept += kept;
          all_truth += truth;
        }
        else
        {
          admitted = all_admitted;
          kept = all_kept;
          truth = all_truth;
        }
        double recall = (truth > 0) ? (double) kept / (double) truth : 1.0;
        fprintf(out, "%s,%s,%s,%" PRId64 ",%" PRId64 ",%" PRId64 ",%.6f\n",
          path_name[p], region_name[coarse_region[j]],
          (k < nwin) ? w[k].name : "all", admitted, truth, kept, recall);
      }
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
  int coarse_res, const Window *w, int nwin, const Measure *m,
  const WindowMeasure *wm)
{
  char tmp[PATH_MAX];
  snprintf(tmp, sizeof(tmp), "%s.part", path);
  FILE *out = fopen(tmp, "w");
  if (out == NULL)
    return;
  emit_results(out, ntrips, stored_res, coarse_res, w, nwin, m, wm);
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
  WindowMeasure *wm = calloc((size_t) (nwin > 0 ? nwin : 1),
    sizeof(WindowMeasure));
  bool *admits = calloc((size_t) (PATH_N * COARSE_REGION_N *
    (nwin > 0 ? nwin : 1)), sizeof(bool));
  hex_nslots = HEX_TABLE_INIT;
  hex_slots = calloc((size_t) hex_nslots, sizeof(HexSlot));
  if (hex_slots == NULL)
  {
    fprintf(stderr, "cannot allocate the hexagon cover table\n");
    return 1;
  }

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
      trip_measure(cur_id, instants, ninst, w, nwin, stored_res, coarse_res,
        &m, wm, admits);
      ntrips++;
      if (ntrips % 1000 == 0)
      {
        fprintf(stderr, "\r  trips %" PRId64, ntrips);
        checkpoint_results(checkpoint_path, ntrips, stored_res, coarse_res,
          w, nwin, &m, wm);
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

  emit_results(stdout, ntrips, stored_res, coarse_res, w, nwin, &m, wm);
  remove(checkpoint_path);
  windows_free(w, nwin);
  free(hex_slots);
  free(hex_cells);
  free(wm);
  free(admits);
  free(instants);
  meos_finalize();
  return 0;
}
