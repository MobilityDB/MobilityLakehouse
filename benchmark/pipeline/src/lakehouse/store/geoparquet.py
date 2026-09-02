# GeoParquet 1.1 helpers

import json

_PROJJSON_UTM32N: dict = {
    "$schema": "https://proj.org/schemas/v0.7/projjson.schema.json",
    "type": "ProjectedCRS",
    "name": "WGS 84 / UTM zone 32N",
    "base_crs": {
        "name": "WGS 84",
        "datum_ensemble": {
            "name": "World Geodetic System 1984 ensemble",
            "members": [
                {"name": "World Geodetic System 1984 (Transit)"},
                {"name": "World Geodetic System 1984 (G730)"},
                {"name": "World Geodetic System 1984 (G873)"},
                {"name": "World Geodetic System 1984 (G1150)"},
                {"name": "World Geodetic System 1984 (G1674)"},
                {"name": "World Geodetic System 1984 (G1762)"},
                {"name": "World Geodetic System 1984 (G2139)"},
            ],
            "ellipsoid": {
                "name": "WGS 84",
                "semi_major_axis": 6378137,
                "inverse_flattening": 298.257223563,
            },
            "accuracy": "2.0",
            "id": {"authority": "EPSG", "code": 6326},
        },
        "coordinate_system": {
            "subtype": "ellipsoidal",
            "axis": [
                {"name": "Geodetic latitude",  "abbreviation": "Lat", "direction": "north", "unit": "degree"},
                {"name": "Geodetic longitude", "abbreviation": "Lon", "direction": "east",  "unit": "degree"},
            ],
        },
        "id": {"authority": "EPSG", "code": 4326},
    },
    "conversion": {
        "name": "UTM zone 32N",
        "method": {"name": "Transverse Mercator", "id": {"authority": "EPSG", "code": 9807}},
        "parameters": [
            {"name": "Latitude of natural origin",   "value": 0,        "unit": "degree", "id": {"authority": "EPSG", "code": 8801}},
            {"name": "Longitude of natural origin",  "value": 9,        "unit": "degree", "id": {"authority": "EPSG", "code": 8802}},
            {"name": "Scale factor at natural origin","value": 0.9996,   "unit": "unity",  "id": {"authority": "EPSG", "code": 8805}},
            {"name": "False easting",                "value": 500000,   "unit": "metre",  "id": {"authority": "EPSG", "code": 8806}},
            {"name": "False northing",               "value": 0,        "unit": "metre",  "id": {"authority": "EPSG", "code": 8807}},
        ],
    },
    "coordinate_system": {
        "subtype": "Cartesian",
        "axis": [
            {"name": "Easting",  "abbreviation": "E", "direction": "east",  "unit": "metre"},
            {"name": "Northing", "abbreviation": "N", "direction": "north", "unit": "metre"},
        ],
    },
    "id": {"authority": "EPSG", "code": 32632},
}

def geo_metadata_json(geometry_col: str = "geometry", bbox_col: str = "bbox") -> str:
    return json.dumps({
        "version": "1.1.0",
        "primary_column": geometry_col,
        "columns": {
            geometry_col: {
                "encoding": "WKB",
                "geometry_types": ["Polygon"],
                "crs": _PROJJSON_UTM32N,
                "edges": "planar",
                "covering": {
                    "bbox": {
                        "xmin": [bbox_col, "xmin"],
                        "ymin": [bbox_col, "ymin"],
                        "xmax": [bbox_col, "xmax"],
                        "ymax": [bbox_col, "ymax"],
                    }
                },
            }
        },
    }, separators=(",", ":"))

def temporal_metadata_json(
    temporal_col: str,
    *,
    base_type: str = "tgeompoint",
    subtype: str = "Sequence",
    interpolation: str = "linear",
    encoding: str = "MEOS-WKB",
    encoding_version: str = "1.0",
    srid: int = 32632,
    geodetic: bool = False,
    has_z: bool = False,
) -> str:
    return json.dumps(
        {
            "version": "1.0.0",
            "primary_temporal_column": temporal_col,
            "columns": {
                temporal_col: {
                    "encoding": encoding,
                    "encoding_version": encoding_version,
                    "base_type": base_type,
                    "subtype": subtype,
                    "interpolation": interpolation,
                    "srid": srid,
                    "geodetic": geodetic,
                    "has_z": has_z,
                }
            },
        },
        separators=(",", ":"),
    )

def temporal_kv_clause(temporal_col: str = "traj", **kwargs) -> str:
    payload = temporal_metadata_json(temporal_col, **kwargs).replace("'", "''")
    return "KV_METADATA { 'temporal': '" + payload + "' }"

def kv_metadata_clause(temporal_col: str = "traj_wkb") -> str:
    geo = geo_metadata_json().replace("'", "''")
    temporal = temporal_metadata_json(temporal_col).replace("'", "''")
    return "KV_METADATA { 'geo': '" + geo + "', 'temporal': '" + temporal + "' }"

GEOPARQUET_PROJECTION = """
    {
        'xmin': bbox_min_x::DOUBLE,
        'ymin': bbox_min_y::DOUBLE,
        'xmax': bbox_max_x::DOUBLE,
        'ymax': bbox_max_y::DOUBLE
    } AS bbox,
    ST_AsWKB(
        ST_MakeEnvelope(bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y)
    )::BLOB AS geometry,
    asBinary(traj)::BLOB AS traj_wkb
"""

GEOPARQUET_EXCLUDE = "bbox_min_x, bbox_min_y, bbox_max_x, bbox_max_y, traj"
