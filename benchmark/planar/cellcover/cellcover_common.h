/*****************************************************************************
 *
 * cellcover_common.h
 *
 * The cell arrays, the region covers and the query windows of the planar
 * corpus's cell-cover experiments.
 *
 * H3 reads geographic latitude and longitude only, so the experiments run in
 * WGS84 throughout: a trip is the corpus trip transformed to EPSG:4326 instant
 * by instant, and a window is the corpus rectangle transformed to EPSG:4326
 * corner by corner. The exact predicate and the covers read those same
 * objects: a trip qualifies when its WGS84 trajectory meets the WGS84 window
 * over the period, and a cover is sound when it keeps every such trip. A cover
 * is a sorted, duplicate-free array of cells, and the question asked of two
 * covers is always whether they share one.
 *
 * Copyright (c) 2026, Esteban Zimanyi, Universite Libre de Bruxelles
 *
 *****************************************************************************/

#ifndef CELLCOVER_COMMON_H
#define CELLCOVER_COMMON_H

#include <stdio.h>
#include <stdbool.h>
#include <inttypes.h>

#include <meos.h>
#include <meos_geo.h>
#include <meos_h3.h>
#include <meos_internal.h>
#include <h3api.h>

/** Seconds between the Unix and the PostgreSQL epochs */
#define UNIX_TO_PG_EPOCH  946684800LL

/** Upper bound on the points a single trip contributes */
#define MAX_TRIP_POINTS   1000000

/** The SRID of the trips and the windows the harness reads, the frame H3 reads */
#define GEO_SRID          4326

/** The trip cover constructions, in the order they are reported */
typedef enum { TRIP_INSTANT, TRIP_SWEPT, TRIP_N } tripCover;
/** The region cover constructions, in the order they are reported */
typedef enum { REGION_CENTRES, REGION_EXACT, REGION_EXACT_RING, REGION_N }
  regionCover;

extern const char *trip_name[TRIP_N];
extern const char *region_name[REGION_N];

/** A query window: a WGS84 polygon over a period, and its region covers */
typedef struct
{
  char    name[64];
  char   *wkt;                      /**< The polygon, WGS84, longitude first */
  int64   t0, t1;                   /**< Unix seconds, inclusive */
  H3Index *cells[REGION_N];         /**< The region covers, sorted */
  int     ncells[REGION_N];
  int     geom;                     /**< Index of the first window with this polygon */
  GSERIALIZED *region;              /**< The exact predicate's polygon */
  Span   *period;                   /**< The exact predicate's period */
} Window;

/** What one (trip cover, region cover) pair answered over the trips */
typedef struct
{
  int64 truth;      /**< pairs the exact predicate accepts */
  int64 kept;       /**< of those, the pairs the cell test also accepts */
  int64 admitted;   /**< pairs the cell test accepts, qualifying or not */
} Tally;

extern int cells_sort_uniq(H3Index *cells, int count);
extern bool cells_intersect(const H3Index *a, int na, const H3Index *b,
  int nb);
extern H3Index *th3index_cells(const Temporal *cells, int *count);
extern H3Index *cells_dilate(const H3Index *in, int nin, int *count);
extern Window *windows_read(const char *path, int resolution, int *count);
extern void windows_free(Window *w, int nwin);

/*****************************************************************************
 * The trips
 *****************************************************************************/

/** One position of one trip, in WGS84 */
typedef struct
{
  int64  id;      /**< trip identifier, ascending, positions grouped by it */
  double lon;
  double lat;
  int64  secs;    /**< Unix seconds */
} TripRow;

/** The bytes a packed trip file opens with, naming the record layout */
#define TRIPS_BIN_MAGIC   "MDBTRIPS3"
/** The prefix the magic of every packed layout shares, this one or an earlier one */
#define TRIPS_BIN_FAMILY  "MDBTRIPS"

/** A source of trip positions, over the text form or the packed one */
typedef struct
{
  FILE    *f;
  bool     binary;
  TripRow *buf;      /**< records read ahead, packed source only */
  size_t   nbuf;     /**< records the buffer holds */
  size_t   pos;      /**< records of it already handed out */
} TripSource;

extern bool trip_source_open(TripSource *src, const char *path);
extern bool trip_source_next(TripSource *src, TripRow *row);
extern void trip_source_close(TripSource *src);
extern bool trips_csv_to_bin(const char *csv_path, const char *bin_path,
  int64 *nrows);

#endif /* CELLCOVER_COMMON_H */
