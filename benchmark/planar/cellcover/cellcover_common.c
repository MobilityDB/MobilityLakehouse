/*****************************************************************************
 *
 * cellcover_common.c
 *
 * The cell arrays, the region covers and the query windows of the planar
 * cell-cover experiments. See cellcover_common.h for what belongs here.
 *
 * Copyright (c) 2026, Esteban Zimanyi, Universite Libre de Bruxelles
 *
 *****************************************************************************/

#include "cellcover_common.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

const char *trip_name[TRIP_N] = { "per-instant", "swept segment" };
const char *region_name[REGION_N] = { "centre containment",
  "intersecting + 1 ring", "intersecting + 2 rings" };

/*****************************************************************************
 * Cell arrays
 *****************************************************************************/

static int
cell_cmp(const void *a, const void *b)
{
  H3Index x = *(const H3Index *) a, y = *(const H3Index *) b;
  return (x < y) ? -1 : (x > y) ? 1 : 0;
}

/**
 * @brief Sort a cell array and drop its duplicates, returning the count kept
 */
int
cells_sort_uniq(H3Index *cells, int count)
{
  if (count <= 0)
    return 0;
  qsort(cells, (size_t) count, sizeof(H3Index), cell_cmp);
  int k = 1;
  for (int i = 1; i < count; i++)
    if (cells[i] != cells[k - 1])
      cells[k++] = cells[i];
  return k;
}

/**
 * @brief Return whether two sorted cell arrays share a cell
 * @details This is the membership the `?=` operator answers between a cell
 * set and a temporal cell value: the trip meets the region cover when some
 * cell it occupies is one the region kept. The smaller array is searched in
 * the larger, since a trip holds thousands of cells and a region cover up to
 * a million at resolution 12.
 */
bool
cells_intersect(const H3Index *a, int na, const H3Index *b, int nb)
{
  if (na <= 0 || nb <= 0)
    return false;
  const H3Index *small = a, *large = b;
  int nsmall = na, nlarge = nb;
  if (nb < na)
  {
    small = b; large = a;
    nsmall = nb; nlarge = na;
  }
  for (int i = 0; i < nsmall; i++)
  {
    int lo = 0, hi = nlarge - 1;
    H3Index key = small[i];
    while (lo <= hi)
    {
      int mid = lo + (hi - lo) / 2;
      if (large[mid] == key)
        return true;
      if (large[mid] < key)
        lo = mid + 1;
      else
        hi = mid - 1;
    }
  }
  return false;
}

/*****************************************************************************
 * The covers
 *****************************************************************************/

/**
 * @brief Return the cells of a temporal cell value, sorted and deduplicated
 * @details `th3index_values` answers the distinct cells in ascending order,
 * so no sort is added here.
 */
H3Index *
th3index_cells(const Temporal *cells, int *count)
{
  return th3index_values(cells, count);
}

/**
 * @brief Read the vertices of a WKT polygon's outer ring as H3 coordinates
 * @details The ring is the one `45_windows.sql` writes: a corpus rectangle
 * transformed to WGS84 corner by corner, longitude first. H3 reads a loop
 * without its closing vertex, so that vertex is dropped, as is any vertex
 * repeating the one before it.
 */
static LatLng *
wkt_ring_latlng(const char *wkt, int *count)
{
  const char *p = strstr(wkt, "((");
  *count = 0;
  if (p == NULL)
    return NULL;
  p += 2;
  int max = 256, n = 0;
  LatLng *v = malloc((size_t) max * sizeof(LatLng));
  while (*p != '\0' && *p != ')')
  {
    char *end;
    double lon = strtod(p, &end);
    if (end == p)
      break;
    p = end;
    double lat = strtod(p, &end);
    if (end == p)
      break;
    p = end;
    while (*p == ',' || isspace((unsigned char) *p))
      p++;
    LatLng ll = { .lat = degsToRads(lat), .lng = degsToRads(lon) };
    if (n > 0 && v[n - 1].lat == ll.lat && v[n - 1].lng == ll.lng)
      continue;
    if (n == max)
    {
      max *= 2;
      v = realloc(v, (size_t) max * sizeof(LatLng));
    }
    v[n++] = ll;
  }
  if (n > 1 && v[0].lat == v[n - 1].lat && v[0].lng == v[n - 1].lng)
    n--;
  *count = n;
  return v;
}

/**
 * @brief Return the region cover holding every cell whose centre lies inside
 * the region
 * @details This is H3's `polygonToCells` on the region's WGS84 boundary. A
 * cell meeting the region without holding its centre is absent, which is
 * every cell along the boundary, so the cover under-approximates the region.
 */
static H3Index *
region_cover_centres(const char *wkt, int resolution, int *count)
{
  int nverts;
  LatLng *verts = wkt_ring_latlng(wkt, &nverts);
  *count = 0;
  if (nverts < 3)
  {
    free(verts);
    return NULL;
  }
  GeoPolygon gp = { .geoloop = { .numVerts = nverts, .verts = verts },
    .numHoles = 0, .holes = NULL };

  int64_t max_cells = 0;
  if (maxPolygonToCellsSize(&gp, resolution, 0, &max_cells) != E_SUCCESS ||
      max_cells <= 0)
  {
    free(verts);
    return NULL;
  }
  H3Index *cells = calloc((size_t) max_cells, sizeof(H3Index));
  if (polygonToCells(&gp, resolution, 0, cells) != E_SUCCESS)
  {
    free(cells);
    free(verts);
    return NULL;
  }
  free(verts);
  int n = 0;
  for (int64_t i = 0; i < max_cells; i++)
    if (cells[i] != (H3Index) 0)
      cells[n++] = cells[i];
  *count = cells_sort_uniq(cells, n);
  /* maxPolygonToCellsSize answers H3's upper bound, not the cell count, and the
   * array is held for the whole run: give back the difference */
  if (*count > 0)
  {
    H3Index *fit = realloc(cells, (size_t) *count * sizeof(H3Index));
    if (fit != NULL)
      cells = fit;
  }
  return cells;
}

/**
 * @brief Return the region cover MEOS builds, every cell the region meets
 * @details This is `geoToH3IndexSet` on the region's WGS84 boundary. The
 * cells are read from the set's text form, each through `h3index_in`, the
 * twin of the `h3index_out` that wrote it.
 */
static H3Index *
region_cover_ring(const char *wkt, int resolution, int *count)
{
  *count = 0;
  size_t len = strlen(wkt) + 16;
  char *ewkt = malloc(len);
  snprintf(ewkt, len, "SRID=%d;%s", GEO_SRID, wkt);
  GSERIALIZED *gs = geom_in(ewkt, -1);
  free(ewkt);
  if (gs == NULL)
    return NULL;
  Set *s = geo_to_h3index_set(gs, resolution);
  free(gs);
  if (s == NULL)
    return NULL;

  int n = set_num_values(s);
  char *text = set_out(s, 0);
  free(s);
  H3Index *cells = calloc((size_t) (n > 0 ? n : 1), sizeof(H3Index));
  int k = 0;
  char token[32];
  const char *p = text;
  while (*p != '\0' && k < n)
  {
    if (! isxdigit((unsigned char) *p))
    {
      p++;
      continue;
    }
    int t = 0;
    while (isxdigit((unsigned char) *p) && t < (int) sizeof(token) - 1)
      token[t++] = *p++;
    token[t] = '\0';
    H3Index cell = h3index_in(token);
    if (cell != (H3Index) 0)
      cells[k++] = cell;
  }
  free(text);
  if (k != n)
    fprintf(stderr, "region cover: read %d of the %d cells of the set\n", k, n);
  *count = cells_sort_uniq(cells, k);
  return cells;
}

/**
 * @brief Return a cover dilated by one further grid ring
 * @details The paper's listing wraps `geoToH3IndexSet` in a `gridDisk` of
 * radius one, on the reading that the constructor answers the cells whose
 * centre falls in the region. It already dilates, so the wrapper adds a
 * second ring. This arm measures what that costs.
 */
H3Index *
cells_dilate(const H3Index *in, int nin, int *count)
{
  if (nin <= 0)
  {
    *count = 0;
    return NULL;
  }
  H3Index *out = calloc((size_t) nin * 7, sizeof(H3Index));
  int n = 0;
  for (int i = 0; i < nin; i++)
  {
    H3Index ring[7];
    memset(ring, 0, sizeof(ring));
    if (gridDisk(in[i], 1, ring) != E_SUCCESS)
    {
      out[n++] = in[i];
      continue;
    }
    for (int k = 0; k < 7; k++)
      if (ring[k] != (H3Index) 0)
        out[n++] = ring[k];
  }
  *count = cells_sort_uniq(out, n);
  return out;
}

/*****************************************************************************
 * Input
 *****************************************************************************/

/** Longest window line: a name, a period and a polygon of a few vertices */
#define WINDOW_LINE_MAX  65536

/**
 * @brief Read the query windows, one `name|t0|t1|wkt` per line
 * @details The period, in Unix seconds, and the polygon, a corpus rectangle
 * transformed to WGS84 corner by corner, are what the exact predicate reads;
 * the polygon is also what the region covers read.
 */
Window *
windows_read(const char *path, int resolution, int *count)
{
  FILE *f = fopen(path, "r");
  if (f == NULL)
  {
    fprintf(stderr, "cannot open %s\n", path);
    exit(1);
  }
  int max = 64, n = 0;
  Window *w = calloc((size_t) max, sizeof(Window));
  char *line = malloc(WINDOW_LINE_MAX);
  while (fgets(line, WINDOW_LINE_MAX, f) != NULL)
  {
    if (line[0] == '#' || line[0] == '\n')
      continue;
    if (n == max)
    {
      max *= 2;
      w = realloc(w, (size_t) max * sizeof(Window));
      memset(w + n, 0, (size_t) (max - n) * sizeof(Window));
    }
    Window *cur = &w[n];
    int off = 0;
    if (sscanf(line, "%63[^|]|%" SCNd64 "|%" SCNd64 "|%n", cur->name,
          &cur->t0, &cur->t1, &off) != 3 || off == 0)
      continue;
    char *wkt = line + off;
    wkt[strcspn(wkt, "\r\n")] = '\0';
    cur->wkt = strdup(wkt);

    /* The windows are a few regions crossed with several periods, so the same
     * polygon recurs. A cover depends only on the polygon and the resolution:
     * an earlier window with this polygon already holds it. */
    cur->geom = n;
    for (int p = 0; p < n; p++)
      if (strcmp(w[p].wkt, cur->wkt) == 0)
      {
        cur->geom = w[p].geom;
        break;
      }

    if (cur->geom == n)
    {
      cur->cells[REGION_CENTRES] = region_cover_centres(cur->wkt, resolution,
        &cur->ncells[REGION_CENTRES]);
      cur->cells[REGION_RING] = region_cover_ring(cur->wkt, resolution,
        &cur->ncells[REGION_RING]);
      cur->cells[REGION_RING2] = cells_dilate(cur->cells[REGION_RING],
        cur->ncells[REGION_RING], &cur->ncells[REGION_RING2]);
    }
    else
      for (int j = 0; j < REGION_N; j++)
      {
        cur->cells[j] = w[cur->geom].cells[j];
        cur->ncells[j] = w[cur->geom].ncells[j];
      }

    size_t len = strlen(cur->wkt) + 16;
    char *ewkt = malloc(len);
    snprintf(ewkt, len, "SRID=%d;%s", GEO_SRID, cur->wkt);
    cur->region = geom_in(ewkt, -1);
    free(ewkt);
    if (cur->region == NULL)
    {
      fprintf(stderr, "window %s: unreadable polygon\n", cur->name);
      exit(1);
    }
    cur->period = tstzspan_make(
      (TimestampTz) ((cur->t0 - UNIX_TO_PG_EPOCH) * 1000000),
      (TimestampTz) ((cur->t1 - UNIX_TO_PG_EPOCH) * 1000000), true, true);
    n++;
  }
  free(line);
  fclose(f);
  *count = n;
  return w;
}

/**
 * @brief Release the windows and the region covers they hold
 * @details Windows sharing a polygon share one cover, so only the
 * representative owns the arrays.
 */
void
windows_free(Window *w, int nwin)
{
  for (int k = 0; k < nwin; k++)
  {
    if (w[k].geom == k)
      for (int j = 0; j < REGION_N; j++)
        free(w[k].cells[j]);
    free(w[k].wkt);
    free(w[k].region);
    free(w[k].period);
  }
  free(w);
}

/*****************************************************************************
 * The trips
 *****************************************************************************/

/** Records a packed source reads ahead in one call */
#define TRIPS_BIN_BATCH  65536

/**
 * @brief Open a source of trip positions over a text or a packed trip file
 * @details The format is read from the file rather than from its name: a file
 * opening with the packed magic is packed, and anything else is the text form.
 */
bool
trip_source_open(TripSource *src, const char *path)
{
  memset(src, 0, sizeof(TripSource));
  src->f = fopen(path, "rb");
  if (src->f == NULL)
    return false;
  char magic[sizeof(TRIPS_BIN_MAGIC) - 1];
  size_t n = fread(magic, 1, sizeof(magic), src->f);
  if (n == sizeof(magic) && memcmp(magic, TRIPS_BIN_MAGIC, sizeof(magic)) == 0)
  {
    src->binary = true;
    src->buf = calloc(TRIPS_BIN_BATCH, sizeof(TripRow));
    return true;
  }
  /* A packed file of another record layout would read as text and yield no
   * position at all, so it is refused rather than read as an empty corpus */
  if (n >= sizeof(TRIPS_BIN_FAMILY) - 1 &&
      memcmp(magic, TRIPS_BIN_FAMILY, sizeof(TRIPS_BIN_FAMILY) - 1) == 0)
  {
    fprintf(stderr, "%s: a packed trip file of another record layout; "
      "repack it with trips_pack\n", path);
    fclose(src->f);
    src->f = NULL;
    return false;
  }
  /* Not packed: the bytes read are the head of the first line */
  rewind(src->f);
  return true;
}

/**
 * @brief Return the next position of the source in the last argument
 * @return True while a position was read, false at the end of the source
 */
bool
trip_source_next(TripSource *src, TripRow *row)
{
  if (src->binary)
  {
    if (src->pos >= src->nbuf)
    {
      src->nbuf = fread(src->buf, sizeof(TripRow), TRIPS_BIN_BATCH, src->f);
      src->pos = 0;
      if (src->nbuf == 0)
        return false;
    }
    *row = src->buf[src->pos++];
    return true;
  }
  char line[256];
  while (fgets(line, sizeof(line), src->f) != NULL)
  {
    if (sscanf(line, "%" SCNd64 ",%lf,%lf,%" SCNd64, &row->id, &row->lon,
          &row->lat, &row->secs) == 4)
      return true;
  }
  return false;
}

/**
 * @brief Release the source
 */
void
trip_source_close(TripSource *src)
{
  if (src->f != NULL)
    fclose(src->f);
  free(src->buf);
  memset(src, 0, sizeof(TripSource));
}

/**
 * @brief Write the positions of a text trip file to a packed one
 * @details The packed file holds the same positions the text one carries, in
 * the same order, as the records a reader takes without parsing. The harness
 * reads a trip as the run of positions sharing an identifier, so the order is
 * checked on the way: identifiers never decrease, and within a trip the time
 * strictly increases. A file breaking either is refused, since a trip split
 * in two runs would be scored twice and two trips under one run as one.
 */
bool
trips_csv_to_bin(const char *csv_path, const char *bin_path, int64 *nrows)
{
  TripSource src;
  if (! trip_source_open(&src, csv_path))
    return false;
  FILE *out = fopen(bin_path, "wb");
  if (out == NULL)
  {
    trip_source_close(&src);
    return false;
  }
  fwrite(TRIPS_BIN_MAGIC, 1, sizeof(TRIPS_BIN_MAGIC) - 1, out);
  TripRow *batch = calloc(TRIPS_BIN_BATCH, sizeof(TripRow));
  size_t n = 0;
  int64 total = 0;
  TripRow row, prev = { -1, 0.0, 0.0, 0 };
  while (trip_source_next(&src, &row))
  {
    if (row.id < prev.id || (row.id == prev.id && row.secs <= prev.secs))
    {
      fprintf(stderr, "position %" PRId64 " out of order: trip %" PRId64
        " at %" PRId64 " after trip %" PRId64 " at %" PRId64 "\n", total,
        row.id, row.secs, prev.id, prev.secs);
      free(batch);
      fclose(out);
      remove(bin_path);
      trip_source_close(&src);
      return false;
    }
    prev = row;
    batch[n++] = row;
    total++;
    if (n == TRIPS_BIN_BATCH)
    {
      fwrite(batch, sizeof(TripRow), n, out);
      n = 0;
    }
  }
  if (n > 0)
    fwrite(batch, sizeof(TripRow), n, out);
  free(batch);
  fclose(out);
  trip_source_close(&src);
  if (nrows != NULL)
    *nrows = total;
  return true;
}
