import os
import sys
from glob import glob
from pathlib import Path

import pyarrow.parquet as pq
from pyarrow.fs import FileSelector

from lakehouse.store import s3io
from lakehouse.query.registry import DATA, TRIPS_DEST, layouts, trips_root
from lakehouse.store.catalog import catalog, table_name, NAMESPACE

TRIPS_ROOT = DATA / "trips"
BUCKET = os.getenv("ICEBERG_WAREHOUSE", "s3://warehouse/").removeprefix("s3://").rstrip("/")

def upload(fs, ls) -> tuple[list[str], list[str]]:
    root = TRIPS_ROOT / ls.subdir
    files = sorted(glob((root / "**" / "*.parquet").as_posix(), recursive=True))
    uris = []
    for f in files:
        rel = Path(f).relative_to(DATA)
        key = f"{BUCKET}/{rel.as_posix()}"
        with open(f, "rb") as src, fs.open_output_stream(key) as dst:
            dst.write(src.read())
        uris.append(f"s3://{key}")
    return files, uris

def register(cat, ls, schema, uris: list[str]) -> None:
    cat.create_namespace_if_not_exists((NAMESPACE,))
    ident = table_name(ls)
    try:
        cat.drop_table(ident)
    except Exception:
        pass
    tbl = cat.create_table(ident, schema=schema)
    tbl.add_files(uris)

def check_bucket(fs) -> None:
    try:
        buckets = [i.base_name for i in fs.get_file_info(FileSelector(""))]
    except Exception as e:
        sys.exit(f"cannot reach the S3 endpoint ({e}).\n"
                 "Is MinIO up?  cd deploy/iceberg_rest && docker compose up -d")
    if BUCKET not in buckets:
        sys.exit(f"bucket s3://{BUCKET}/ does not exist (found: {', '.join(buckets) or 'none'}).\n"
                 "The compose file creates it; bring the stack up with\n"
                 "  cd deploy/iceberg_rest && docker compose up -d\n"
                 f"or create it by hand:  mc mb local/{BUCKET}")

def main() -> None:
    args = set(sys.argv[1:])
    all_layouts = list(layouts())
    keys = {l.key for l in layouts()} if (not args or args == {"all"}) else args
    if os.getenv("ICEBERG_CATALOG_TYPE") != "rest":
        sys.exit("Set REST env first:  source deploy/iceberg_rest/env.rest")
    check_bucket(s3io.s3_fs())
    cat = catalog()
    on_s3 = s3io.is_s3(TRIPS_DEST)
    fs = None if on_s3 else s3io.s3_fs()
    for ls in all_layouts:
        if ls.key not in keys:
            continue
        if on_s3:
            uris = s3io.list_parquet(trips_root(ls))
            if not uris:
                print(f"  {ls.key:<12} no trips parquet on s3; skipped", flush=True)
                continue
            schema = pq.ParquetFile(uris[0].removeprefix("s3://"),
                                    filesystem=s3io.s3_fs()).schema_arrow
            register(cat, ls, schema, uris)
            verb = "registered (in place)"
        else:
            files, uris = upload(fs, ls)
            if not files:
                print(f"  {ls.key:<12} no trips parquet found; skipped", flush=True)
                continue
            schema = pq.ParquetFile(files[0]).schema_arrow
            register(cat, ls, schema, uris)
            verb = "uploaded+registered"
        print(f"  {ls.key:<12} {verb} {len(uris):>5} files  -> {table_name(ls)}", flush=True)
    print(f"\nDone. Tables under REST catalog namespace {NAMESPACE!r} on s3://{BUCKET}/")

if __name__ == "__main__":
    main()
