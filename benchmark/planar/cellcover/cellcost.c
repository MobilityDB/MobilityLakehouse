/*****************************************************************************
 *
 * cellcost.c
 *
 * What a stored cell cover removes before the exact predicate, and what it
 * costs, over one layout's moving rows carried to WGS84.
 *
 * A query runs in three phases: the scalar box of each row, as the layout
 * stores it, keeps the rows whose box meets the query rectangle over the
 * period; the cell cover keeps, of those, the rows whose stored cells meet the
 * query region's cover dilated by one ring, without decoding a trajectory; the
 * exact predicate decides the rest. This program counts, per query window, the
 * candidates each phase leaves, times the exact predicate over the box's
 * candidates and over the cover's, and times the cover test itself, and it
 * checks that the cover keeps every candidate the exact predicate accepts. It
 * also sizes the cell column against the trajectory column beside it, both as
 * extended WKB, and times building the cover, which a layout pays once.
 *
 * The rows are those 46_trips.sh exports for a layout: their positions in
 * WGS84 and, beside them, their covering bounds in the corpus's metric frame.
 * The windows are windows.psv, the WGS84 polygons the exact predicate and the
 * region covers read, and windows_25832.csv, the metric rectangles the box
 * filter reads, matched by name. The row loop and the exact predicate are
 * cellcover.c's, the timing and the result block coarsen.c's.
 *
 * Copyright (c) 2026, Esteban Zimanyi, Universite Libre de Bruxelles
 *
 *****************************************************************************/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "cellcover_common.h"

/** The covering bounds of one row, in the corpus's metric frame */
typedef struct
{
  int64  id;
  double xmin, ymin, xmax, ymax;
  int64  t0, t1;              /**< Unix seconds, the span rounded outwards */
} RowBox;

/** A query rectangle in the corpus's metric frame */
typedef struct
{
  double xmin, ymin, xmax, ymax;
} Rect;

/** What one window measured, accumulated over the rows */
typedef struct
{
  int64  box;                 /**< rows the box filter keeps */
  int64  cover;               /**< of those, the rows the cover also keeps */
  int64  truth;               /**< of the box's rows, those the exact predicate accepts */
  int64  kept;                /**< of those, the rows the cover keeps */
  int64  errors;              /**< rows the exact predicate cannot decide */
  double exact_box;           /**< seconds of the exact predicate over the box's rows */
  double exact_cover;         /**< seconds of it over the cover's rows */
  double test;                /**< seconds of the cover test over the box's rows */
} Cost;

/** What the rows measured whatever the window */
typedef struct
{
  int64  rows;
  size_t cover_bytes;         /**< the cell column, extended WKB */
  size_t trip_bytes;          /**< the trajectory column, extended WKB */
  double build;               /**< seconds building the covers */
} Column;

static double
now_seconds(void)
{
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (double) t.tv_sec + (double) t.tv_nsec / 1e9;
}

/**
 * @brief Return the rows' covering bounds, in trip order
 */
static RowBox *
boxes_read(const char *path, int64 *count)
{
  FILE *f = fopen(path, "r");
  if (f == NULL)
    return NULL;
  int64 cap = 1 << 16, n = 0;
  RowBox *b = malloc((size_t) cap * sizeof(RowBox));
  char line[512];
  while (fgets(line, sizeof(line), f) != NULL)
  {
    if (n == cap)
    {
      cap *= 2;
      b = realloc(b, (size_t) cap * sizeof(RowBox));
    }
    RowBox *r = &b[n];
    if (sscanf(line, "%" SCNd64 ",%lf,%lf,%lf,%lf,%" SCNd64 ",%" SCNd64, &r->id,
        &r->xmin, &r->ymin, &r->xmax, &r->ymax, &r->t0, &r->t1) == 7)
      n++;
  }
  fclose(f);
  *count = n;
  return b;
}

/**
 * @brief Read the metric rectangle of every window, matched by name
 */
static bool
rects_read(const char *path, const Window *w, int nwin, Rect *rect)
{
  bool *found = calloc((size_t) (nwin > 0 ? nwin : 1), sizeof(bool));
  FILE *f = fopen(path, "r");
  if (f == NULL)
  {
    free(found);
    return false;
  }
  char line[512];
  while (fgets(line, sizeof(line), f) != NULL)
  {
    char name[64];
    double x0, y0, x1, y1;
    if (sscanf(line, "%63[^,],%lf,%lf,%lf,%lf", name, &x0, &y0, &x1, &y1) != 5)
      continue;
    for (int k = 0; k < nwin; k++)
      if (strcmp(w[k].name, name) == 0)
      {
        rect[k] = (Rect) { x0, y0, x1, y1 };
        found[k] = true;
      }
  }
  fclose(f);
  bool all = true;
  for (int k = 0; k < nwin; k++)
    if (! found[k])
    {
      fprintf(stderr, "no rectangle for window %s in %s\n", w[k].name, path);
      all = false;
    }
  free(found);
  return all;
}

/**
 * @brief Measure one row against every window
 */
static void
row_measure(TInstant **inst, int ninst, const RowBox *b, const Window *w,
  const Rect *rect, int nwin, int resolution, Cost *cost, Column *col)
{
  TSequence *seq = tsequence_make(inst, ninst, true, true, LINEAR, true);
  const Temporal *trip = (const Temporal *) seq;

  /* The stored column: built once per row, at build time */
  double t0 = now_seconds();
  Temporal *stored = tgeompoint_to_th3index(trip, resolution);
  col->build += now_seconds() - t0;
  size_t size = 0;
  uint8_t *wkb = temporal_as_wkb(stored, WKB_EXTENDED, &size);
  col->cover_bytes += size;
  free(wkb);
  wkb = temporal_as_wkb(trip, WKB_EXTENDED, &size);
  col->trip_bytes += size;
  free(wkb);
  int ncells;
  H3Index *cells = th3index_cells(stored, &ncells);
  col->rows++;

  for (int k = 0; k < nwin; k++)
  {
    /* Phase one: the box the layout stores meets the rectangle over the period */
    const Rect *r = &rect[k];
    if (b->xmax < r->xmin || b->xmin > r->xmax || b->ymax < r->ymin ||
        b->ymin > r->ymax || b->t1 < w[k].t0 || b->t0 > w[k].t1)
      continue;
    Cost *c = &cost[k];
    c->box++;

    /* Phase two: the stored cells meet the region's dilated cover */
    double a = now_seconds();
    bool passes = cells_intersect(cells, ncells, w[k].cells[REGION_RING],
      w[k].ncells[REGION_RING]);
    double e0 = now_seconds();
    c->test += e0 - a;

    /* Phase three: the trip over the window's period meets its polygon. An
     * answer the predicate cannot give is counted apart, never read as a row
     * that does not qualify. */
    Temporal *during = temporal_at_tstzspan(trip, w[k].period);
    bool qualifies = false;
    if (during != NULL)
    {
      int meets = eintersects_tgeo_geo(during, w[k].region);
      if (meets < 0)
        c->errors++;
      qualifies = (meets == 1);
      free(during);
    }
    double e = now_seconds() - e0;
    c->exact_box += e;
    if (passes)
    {
      c->cover++;
      c->exact_cover += e;
    }
    if (qualifies)
    {
      c->truth++;
      if (passes)
        c->kept++;
    }
  }
  free(cells);
  free(stored);
  free(seq);
}

/**
 * @brief Write the result block, one line per window and the sum over them
 */
static void
emit_results(FILE *out, int resolution, const Window *w, int nwin,
  const Cost *cost, const Column *col)
{
  fprintf(out, "rows,%" PRId64 "\n", col->rows);
  fprintf(out, "resolution,%d\n", resolution);
  fprintf(out, "cover_build_seconds,%.3f\n", col->build);
  fprintf(out, "cover_bytes,%zu\n", col->cover_bytes);
  fprintf(out, "trip_bytes,%zu\n", col->trip_bytes);
  fprintf(out, "column_pct,%.3f\n", (col->trip_bytes > 0) ?
    100.0 * (double) col->cover_bytes / (double) col->trip_bytes : 0.0);
  fprintf(out, "window,box,cover,reduction,truth,kept,errors,exact_box_s,"
    "exact_cover_s,test_s\n");
  Cost all;
  memset(&all, 0, sizeof(all));
  for (int k = 0; k <= nwin; k++)
  {
    const Cost *c = (k < nwin) ? &cost[k] : &all;
    fprintf(out, "%s,%" PRId64 ",%" PRId64 ",%.3f,%" PRId64 ",%" PRId64 ",%" PRId64
      ",%.3f,%.3f,%.3f\n", (k < nwin) ? w[k].name : "all", c->box, c->cover,
      (c->cover > 0) ? (double) c->box / (double) c->cover : 0.0, c->truth,
      c->kept, c->errors, c->exact_box, c->exact_cover, c->test);
    if (k < nwin)
    {
      all.box += c->box;
      all.cover += c->cover;
      all.truth += c->truth;
      all.kept += c->kept;
      all.errors += c->errors;
      all.exact_box += c->exact_box;
      all.exact_cover += c->exact_cover;
      all.test += c->test;
    }
  }
}

int
main(int argc, char **argv)
{
  if (argc < 6)
  {
    fprintf(stderr, "usage: %s <trips.csv|trips.bin> <box.csv> <windows.psv> "
      "<windows_25832.csv> <resolution>\n"
      "  trips.csv          trip_id,lon,lat,unix_seconds  WGS84, ordered by trip_id then time\n"
      "  box.csv            trip_id,xmin,ymin,xmax,ymax,tmin_unix,tmax_unix  metric frame\n"
      "  windows.psv        name|t0_unix|t1_unix|wgs84_polygon_wkt\n"
      "  windows_25832.csv  name,xmin,ymin,xmax,ymax,t0,t1  metric frame\n", argv[0]);
    return 1;
  }
  int resolution = atoi(argv[5]);

  meos_initialize();
  meos_initialize_timezone("UTC");

  int nwin;
  Window *w = windows_read(argv[3], resolution, &nwin);
  Rect *rect = calloc((size_t) (nwin > 0 ? nwin : 1), sizeof(Rect));
  if (! rects_read(argv[4], w, nwin, rect))
    return 1;
  int64 nbox;
  RowBox *boxes = boxes_read(argv[2], &nbox);
  if (boxes == NULL)
  {
    fprintf(stderr, "cannot read %s\n", argv[2]);
    return 1;
  }
  fprintf(stderr, "windows: %d, rows with bounds: %" PRId64 ", resolution %d\n", nwin,
    nbox, resolution);

  TripSource src;
  if (! trip_source_open(&src, argv[1]))
  {
    fprintf(stderr, "cannot open %s\n", argv[1]);
    return 1;
  }
  Cost *cost = calloc((size_t) (nwin > 0 ? nwin : 1), sizeof(Cost));
  Column col;
  memset(&col, 0, sizeof(col));
  TInstant **inst = calloc(MAX_TRIP_POINTS, sizeof(TInstant *));
  int ninst = 0;
  int64 cur_id = -1;

  while (true)
  {
    TripRow r = { 0, 0.0, 0.0, 0 };
    bool parsed = trip_source_next(&src, &r);
    bool eof = ! parsed;

    if ((eof || r.id != cur_id) && ninst >= 1)
    {
      /* The bounds file lists the rows in trip order, one per identifier from 1 */
      int64 idx = cur_id - 1;
      if (idx < 0 || idx >= nbox || boxes[idx].id != cur_id)
      {
        fprintf(stderr, "no bounds for row %" PRId64 "\n", cur_id);
        return 1;
      }
      row_measure(inst, ninst, &boxes[idx], w, rect, nwin, resolution, cost, &col);
      if (col.rows % 10000 == 0)
        fprintf(stderr, "\r  rows %" PRId64, col.rows);
    }
    if (eof || r.id != cur_id)
    {
      for (int i = 0; i < ninst; i++)
        free(inst[i]);
      ninst = 0;
      cur_id = r.id;
    }
    if (eof)
      break;
    if (ninst == MAX_TRIP_POINTS)
      continue;

    TimestampTz t = (TimestampTz) ((r.secs - UNIX_TO_PG_EPOCH) * 1000000);
    GSERIALIZED *gs = geompoint_make2d(GEO_SRID, r.lon, r.lat);
    inst[ninst++] = tpointinst_make(gs, t);
    free(gs);
  }
  trip_source_close(&src);
  fprintf(stderr, "\r  rows %" PRId64 "\n", col.rows);

  emit_results(stdout, resolution, w, nwin, cost, &col);
  windows_free(w, nwin);
  free(rect);
  free(boxes);
  free(cost);
  free(inst);
  meos_finalize();
  return 0;
}
