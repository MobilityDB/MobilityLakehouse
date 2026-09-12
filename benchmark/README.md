# The planar benchmark

The experiments of the lakehouse paper run on the AIS reports the Danish Maritime Authority (DMA)
publishes, one archive per day. The scripts here take a period of those archives to the L0 base
layer of TemporalParquet trips in EPSG:25832 and to the eleven layouts the paper compares, and
check every layout before a figure is read from it.

## The data

The data are DMA's and are not redistributed with this repository. They are published at
<http://aisdata.ais.dk/> as `aisdk-YYYY-MM-DD.zip`, one CSV per day. The paper measures January
2026, the 31 archives `aisdk-2026-01-01.zip` to `aisdk-2026-01-31.zip`, 17.33 GB in all; one week
of it, 2026-01-15 to 2026-01-21, is 4.15 GB. `ingest/fetch_dma.sh` downloads a period onto the
machine that runs the benchmark, into the repository's `data/` directory, which git ignores:

```bash
benchmark/ingest/fetch_dma.sh 2026-01-01 2026-01-31           # the month
benchmark/ingest/fetch_dma.sh 2026-01-15 2026-01-21 --check   # the size of a week, nothing downloaded
```

A download is renamed into place only once it is complete, and a day already downloaded or already
in the raw zone is skipped, so an interrupted download resumes where it stopped.

## Requirements

- A DuckDB shell carrying the MobilityDuck extension, built from
  <https://github.com/MobilityDB/MobilityDuck>, named by `DUCKDB_ENGINE` or by a local file
  `planar/engine.path` holding its path (git ignores it).
- `bash`, `curl` and `unzip`.
- Disk: for the month, 17.3 GB of archives, 11 GB of raw zone, and in the run directory 9.0 GB of
  vessel buckets, 9.2 GB of clean points, 8.0 GB of segments, 3.3 GB of L0, 23.0 GB of daily
  layouts and 12.6 GB of compact layouts.

## Reproducing

```bash
benchmark/reproduce.sh 2026-01-01 2026-01-31
```

runs, for the days given, both included, the raw zone of every day not yet in it
(`ingest/raw_zone.sh`), the cleaning, segmentation and L0 (`planar/run_clean.sh`), the layouts
(`planar/40_order_layouts.sh`, `planar/41_part_layouts.sh`, `planar/42_compact_layouts.sh`) and
their checks (`planar/92_check_layouts.sh`). Without arguments it runs the paper's month. It stops
and names the download command when a day of the period is missing. The run lands in
`data/stage/planar/<FROM>_<TO + 1 day>/`: `L0/`, `layouts_daily/` (L0X, L0Z, L0H, L1 to L4) and
`layout_compact/` (L1s to L4s). `STEPS` selects the steps an invocation runs, for instance
`STEPS="layouts check"` to rebuild the layouts of a run whose L0 exists.
