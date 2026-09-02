from __future__ import annotations

import os

from pyarrow import fs

def is_s3(path: str) -> bool:
    return str(path).startswith("s3://")

ENDPOINT_ENV = "ICEBERG_CATALOG_PROP__S3__ENDPOINT"
DEFAULT_ENDPOINT = "http://localhost:9000"

def _endpoint_url() -> str:
    return os.environ.get(ENDPOINT_ENV) or DEFAULT_ENDPOINT

def _use_ssl() -> bool:
    return _endpoint_url().startswith("https://")

def _endpoint() -> str:
    return _endpoint_url().removeprefix("http://").removeprefix("https://")

def _key(env: str, default: str) -> str:
    return os.environ.get(env, default)

def attach_s3(con) -> None:
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"""
        CREATE OR REPLACE SECRET s3lakehouse (
            TYPE S3,
            KEY_ID '{_key("ICEBERG_CATALOG_PROP__S3__ACCESS_KEY_ID", "admin")}',
            SECRET '{_key("ICEBERG_CATALOG_PROP__S3__SECRET_ACCESS_KEY", "password")}',
            ENDPOINT '{_endpoint()}',
            URL_STYLE 'path', USE_SSL {'true' if _use_ssl() else 'false'},
            REGION '{_key("ICEBERG_CATALOG_PROP__S3__REGION", "us-east-1")}'
        );
    """)

def s3_fs() -> fs.S3FileSystem:
    return fs.S3FileSystem(
        access_key=_key("ICEBERG_CATALOG_PROP__S3__ACCESS_KEY_ID", "admin"),
        secret_key=_key("ICEBERG_CATALOG_PROP__S3__SECRET_ACCESS_KEY", "password"),
        endpoint_override=_endpoint(),
        scheme="https" if _use_ssl() else "http",
        region=_key("ICEBERG_CATALOG_PROP__S3__REGION", "us-east-1"),
    )

def _prefix(uri: str) -> str:
    return uri.removeprefix("s3://").rstrip("/")

def clear_prefix(uri: str) -> None:
    s3 = s3_fs()
    sel = fs.FileSelector(_prefix(uri), recursive=True, allow_not_found=True)
    for info in s3.get_file_info(sel):
        if info.type == fs.FileType.File:
            s3.delete_file(info.path)

def list_parquet(uri: str) -> list[str]:
    s3 = s3_fs()
    sel = fs.FileSelector(_prefix(uri), recursive=True, allow_not_found=True)
    return sorted(
        "s3://" + i.path for i in s3.get_file_info(sel)
        if i.type == fs.FileType.File and i.path.endswith(".parquet")
    )
