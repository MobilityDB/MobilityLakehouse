# Evaluation config: the layouts under test and where their data lives
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# benchmark/ : the pipeline is installed from benchmark/pipeline, but data,
# queries and results sit beside it under benchmark/.
BENCH_ROOT = Path(os.getenv("LAKEHOUSE_ROOT")
                  or Path(__file__).resolve().parents[4])
DATA = BENCH_ROOT / "data"
RESULTS = BENCH_ROOT / "results"

TRIPS_DEST = os.getenv("TRIPS_DEST", (DATA / "trips").as_posix())

try:
    from lakehouse.settings import Settings

    _S = Settings.create()
    MOBILITYDUCK_EXT = _S.mobilityduck.extension_path
    METRIC_EPSG = _S.pipeline.metric_epsg
except Exception:
    MOBILITYDUCK_EXT = None
    METRIC_EPSG = int(os.getenv("METRIC_EPSG", "32632"))

MOBILITYDUCK_EXT = MOBILITYDUCK_EXT or os.getenv("MOBILITYDUCK_EXT", "")
if not MOBILITYDUCK_EXT:
    raise RuntimeError(
        "MobilityDuck extension path is not configured. Set "
        "MOBILITYDUCK__EXTENSION_PATH in .env (copy .env.example) "
        "or export MOBILITYDUCK_EXT."
    )

@dataclass(frozen=True)
class LayoutSpec:
    name: str
    gran: str
    subdir: str
    partition_keys: tuple[str, ...]
    region_size_m: float | None
    desc: str

    @property
    def root(self) -> Path:
        return DATA / self.subdir

    @property
    def key(self) -> str:
        return self.name if self.name in ("L0", "L0X", "L0Z", "L0H",
                                           "L1s", "L2s", "L3s", "L4s") \
            else f"{self.name}_{self.gran}"

def trips_root(ls: "LayoutSpec") -> str:
    return f"{TRIPS_DEST.rstrip('/')}/{ls.subdir}"

BASE_LAYOUTS: tuple[LayoutSpec, ...] = (
    LayoutSpec("L0", "daily", "L0/L0", ("year", "month", "day"), None,
               "base segments, daily (baseline)"),
    LayoutSpec("L0X", "daily", "layouts_daily/L0X", ("year", "month", "day"), None,
               "daily L0 + lexicographic (cell+time) in-file sort, small row-groups"),
    LayoutSpec("L0Z", "daily", "layouts_daily/L0Z", ("year", "month", "day"), None,
               "daily L0 + Z-order (Morton) in-file sort, small row-groups"),
    LayoutSpec("L0H", "daily", "layouts_daily/L0H", ("year", "month", "day"), None,
               "daily L0 + Hilbert in-file sort, small row-groups"),
    LayoutSpec("L1", "daily", "layouts_daily/L1", ("shard",), None,
               "hash(mmsi), daily"),
    LayoutSpec("L2", "daily", "layouts_daily/L2", ("region_x", "region_y"),
               50_000, "spaceSplit tiles, daily"),
    LayoutSpec("L3", "daily", "layouts_daily/L3", ("region_x", "region_y"),
               50_000, "MEST tiles, daily"),
    LayoutSpec("L4", "daily", "layouts_daily/L4", ("hour",), None,
               "timeSplit(hour), daily"),
    LayoutSpec("L1s", "compact", "layout_compact/L1s", ("shard",), None,
               "hash(mmsi), compact, sorted (cell+time) small row-groups"),
    LayoutSpec("L2s", "compact", "layout_compact/L2s",
               ("region_x", "region_y"), 50_000,
               "spaceSplit tiles, compact, sorted (cell+time) small row-groups"),
    LayoutSpec("L3s", "compact", "layout_compact/L3s",
               ("region_x", "region_y"), 50_000,
               "MEST tiles, compact, sorted (cell+time) small row-groups"),
    LayoutSpec("L4s", "compact", "layout_compact/L4s", ("hour",), None,
               "timeSplit(hour), compact, sorted (cell+time) small row-groups"),
)

SWEEP_LAYOUTS: tuple[LayoutSpec, ...] = (
    LayoutSpec("L2d25", "daily", "sweep_daily_25km/L2",
               ("region_x", "region_y"), 25_000, "spaceSplit tiles 25 km, daily"),
    LayoutSpec("L3d25", "daily", "sweep_daily_25km/L3",
               ("region_x", "region_y"), 25_000, "MEST tiles 25 km, daily"),
    LayoutSpec("L2d100", "daily", "sweep_daily_100km/L2",
               ("region_x", "region_y"), 100_000, "spaceSplit tiles 100 km, daily"),
    LayoutSpec("L3d100", "daily", "sweep_daily_100km/L3",
               ("region_x", "region_y"), 100_000, "MEST tiles 100 km, daily"),
    LayoutSpec("L2c25", "compact", "sweep_compact_25km/L2",
               ("region_x", "region_y"), 25_000, "spaceSplit tiles 25 km, compact"),
    LayoutSpec("L3c25", "compact", "sweep_compact_25km/L3",
               ("region_x", "region_y"), 25_000, "MEST tiles 25 km, compact"),
    LayoutSpec("L2c100", "compact", "sweep_compact_100km/L2",
               ("region_x", "region_y"), 100_000, "spaceSplit tiles 100 km, compact"),
    LayoutSpec("L3c100", "compact", "sweep_compact_100km/L3",
               ("region_x", "region_y"), 100_000, "MEST tiles 100 km, compact"),
)

LAYOUTS: tuple[LayoutSpec, ...] = (
    BASE_LAYOUTS + SWEEP_LAYOUTS if os.getenv("LAKEHOUSE_SWEEP") else BASE_LAYOUTS
)

def _layout_matches(ls: LayoutSpec, names: set[str] | None) -> bool:
    if not names:
        return True
    return ls.name in names or ls.key in names

def layouts(names: set[str] | None = None, grans: set[str] | None = None):
    for ls in LAYOUTS:
        if not _layout_matches(ls, names):
            continue
        if grans and ls.gran not in grans:
            continue
        yield ls
