from __future__ import annotations

import os

from pyiceberg.catalog import load_catalog

from lakehouse.query.registry import LayoutSpec

CATALOG_NAME = os.getenv("ICEBERG_CATALOG_NAME", "ais_bench")
CATALOG_TYPE = os.getenv("ICEBERG_CATALOG_TYPE", "rest").lower()
NAMESPACE = os.getenv("ICEBERG_NAMESPACE", "ais")

def _extra_catalog_props() -> dict[str, str]:
    props: dict[str, str] = {}
    prefix = "ICEBERG_CATALOG_PROP__"
    for key, value in os.environ.items():
        if key.startswith(prefix):
            prop = key[len(prefix):].lower().replace("__", ".").replace("_", "-")
            props[prop] = value
    return props

def catalog():
    if CATALOG_TYPE != "rest":
        raise ValueError(
            f"ICEBERG_CATALOG_TYPE must be 'rest', got {CATALOG_TYPE!r}. "
            f"Did you `source deploy/iceberg_rest/env.rest`?"
        )
    rest_uri = os.getenv("ICEBERG_REST_URI")
    if not rest_uri:
        return load_catalog(CATALOG_NAME)

    props = {
        "type": "rest",
        "uri": rest_uri,
        "warehouse": os.getenv("ICEBERG_WAREHOUSE", "s3://warehouse/"),
    }
    optional_env = {
        "credential": "ICEBERG_REST_CREDENTIAL",
        "token": "ICEBERG_REST_TOKEN",
        "oauth2-server-uri": "ICEBERG_REST_OAUTH2_SERVER_URI",
        "scope": "ICEBERG_REST_SCOPE",
    }
    for prop, env_name in optional_env.items():
        if value := os.getenv(env_name):
            props[prop] = value
    props.update(_extra_catalog_props())
    return load_catalog(CATALOG_NAME, **props)

def table_name(ls: LayoutSpec) -> str:
    return f"{NAMESPACE}.{ls.key}"
