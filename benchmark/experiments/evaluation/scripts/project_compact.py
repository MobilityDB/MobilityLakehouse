# Project the *unsorted* compact layouts (data/layout_compact/L1..L4) to the
from __future__ import annotations

import sys
from lakehouse.query.registry import LayoutSpec, trips_root  # noqa: E402
from build_trips import _con, project_layout  # noqa: E402

def spec(name: str) -> LayoutSpec:
    return LayoutSpec(
        name=name,
        gran="compact",
        subdir=f"layout_compact/{name}",
        partition_keys=(("shard",) if name in ("L1",)
                        else ("hour",) if name == "L4"
                        else ("region_x", "region_y")),
        region_size_m=50_000.0 if name in ("L2", "L3") else None,
        desc=f"unsorted compact {name} (source for {name}s)",
    )

def main() -> None:
    names = sys.argv[1:] or ["L1", "L2", "L3", "L4"]
    con = _con()
    for name in names:
        ls = spec(name)
        if not ls.root.exists():
            print(f"  {name:<4} no local source at {ls.root}; skipped")
            continue
        n, sb, db = project_layout(con, ls)
        pct = 100 * db / sb if sb else 0
        print(f"  {name:<4} {n:>5} files  {sb/1e6:8.1f} -> {db/1e6:8.1f} MB "
              f"({pct:5.1f}%)  -> {trips_root(ls)}", flush=True)

if __name__ == "__main__":
    main()
