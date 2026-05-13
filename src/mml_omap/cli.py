"""Command line interface for exporting MML open data to GeoJSON.

The primary output is GeoJSON in EPSG:3067 coordinates. When the default mapping
is enabled, features also get `symbol` and `object_type` properties.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import os
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path
from typing import Any


MML_OGC_PROCESSES_URL = (
    "https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1"
)
A3_WIDTH_MM = 420.0
A3_HEIGHT_MM = 297.0
MAX_ORIENTEERING_SCALE = 15000
EPSG3067_FALSE_EASTING = 500000.0
EPSG3067_FALSE_NORTHING = 0.0
EPSG3067_SCALE = 0.9996
EPSG3067_CENTRAL_MERIDIAN_DEG = 27.0
GRS80_A = 6378137.0
GRS80_INV_F = 298.257222101

DEFAULT_TABLE_RULES: dict[str, dict[str, Any]] = {
    "tieviiva": {
        "object_type": "line",
        "symbol": "path",
        "kohdeluokka": {
            # Roads and streets.
            "12111": "road",
            "12112": "road",
            "12121": "road",
            "12122": "road",
            "12131": "road",
            "12132": "road",
            "12141": "road",
            "12151": "road",
            "12152": "road",
            # Tracks, paths, footways.
            "12312": "path",
            "12313": "path",
            "12314": "path",
            "12316": "path",
        },
    },
    "rautatie": {"object_type": "line", "symbol": "road"},
    "aita": {"object_type": "line", "symbol": "fence"},
    "jyrkanne": {"object_type": "line", "symbol": "cliff"},
    "virtavesikapea": {"object_type": "line", "symbol": "stream"},
    "korkeuskayra": {"object_type": "line", "symbol": "contour"},
    "rakennusreunaviiva": {"object_type": "line", "symbol": "building"},
    "jarvi": {"object_type": "area", "symbol": "lake"},
    "meri": {"object_type": "area", "symbol": "lake"},
    "virtavesialue": {"object_type": "area", "symbol": "river"},
    "suo": {"object_type": "area", "symbol": "swamp"},
    "soistuma": {"object_type": "area", "symbol": "swamp"},
    "maatalousmaa": {"object_type": "area", "symbol": "field"},
    "niitty": {"object_type": "area", "symbol": "field"},
    "muuavoinalue": {"object_type": "area", "symbol": "field"},
    "puisto": {"object_type": "area", "symbol": "field"},
    "urheilujavirkistysalue": {"object_type": "area", "symbol": "field"},
    "kallioalue": {"object_type": "area", "symbol": "open_rock"},
    "rakennus": {"object_type": "area", "symbol": "building"},
    "kivi": {"object_type": "point", "symbol": "mapped_rock"},
}

SYMBOL_STYLES = {
    "contour": {"stroke": "#9b5a28", "stroke_width_mm": 0.14, "fill": "none"},
    "index_contour": {"stroke": "#9b5a28", "stroke_width_mm": 0.25, "fill": "none"},
    "form_line": {"stroke": "#9b5a28", "stroke_width_mm": 0.10, "fill": "none", "dasharray": "1.0 0.5"},
    "depression_contour": {"stroke": "#9b5a28", "stroke_width_mm": 0.14, "fill": "none"},
    "path": {"stroke": "#000000", "stroke_width_mm": 0.18, "fill": "none", "dasharray": "1.0 0.7"},
    "road": {"stroke": "#000000", "stroke_width_mm": 0.35, "fill": "none"},
    "stream": {"stroke": "#008fd5", "stroke_width_mm": 0.18, "fill": "none"},
    "river": {"stroke": "#008fd5", "stroke_width_mm": 0.35, "fill": "none"},
    "cliff": {"stroke": "#000000", "stroke_width_mm": 0.35, "fill": "none"},
    "fence": {"stroke": "#000000", "stroke_width_mm": 0.18, "fill": "none"},
    "lake": {"stroke": "#008fd5", "stroke_width_mm": 0.10, "fill": "#b9e3f7"},
    "swamp": {"stroke": "#008fd5", "stroke_width_mm": 0.10, "fill": "#d8f0e8"},
    "field": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#f2c84b"},
    "thick_forest": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#49a64a"},
    "very_thick_forest": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#16702f"},
    "open_rock": {"stroke": "#777777", "stroke_width_mm": 0.08, "fill": "#d9d9d9"},
    "building": {"stroke": "#000000", "stroke_width_mm": 0.10, "fill": "#222222"},
    "mapped_rock": {"stroke": "#000000", "stroke_width_mm": 0.10, "fill": "#000000"},
}

DEFAULT_STYLE = {"stroke": "#444444", "stroke_width_mm": 0.18, "fill": "none"}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def parse_bbox(raw: str) -> list[float]:
    parts = [float(part.strip()) for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox must be min_x,min_y,max_x,max_y in EPSG:3067 meters")
    if parts[2] <= parts[0] or parts[3] <= parts[1]:
        raise ValueError("--bbox max values must be greater than min values")
    return parts


def validate_orienteering_bbox_size(bbox: list[float]) -> None:
    width_m = bbox[2] - bbox[0]
    height_m = bbox[3] - bbox[1]
    max_long_side_m = A3_WIDTH_MM * MAX_ORIENTEERING_SCALE / 1000.0
    max_short_side_m = A3_HEIGHT_MM * MAX_ORIENTEERING_SCALE / 1000.0
    long_side_m = max(width_m, height_m)
    short_side_m = min(width_m, height_m)
    if long_side_m > max_long_side_m or short_side_m > max_short_side_m:
        raise ValueError(
            "Requested rectangle is too large for an orienteering map: "
            f"{width_m:.0f} m x {height_m:.0f} m exceeds A3 at 1:{MAX_ORIENTEERING_SCALE} "
            f"({max_long_side_m:.0f} m x {max_short_side_m:.0f} m)"
        )


def oriented_bbox_corners(bbox: list[float], magnetic_declination_deg: float) -> list[tuple[float, float]]:
    center_x = (bbox[0] + bbox[2]) / 2.0
    center_y = (bbox[1] + bbox[3]) / 2.0
    half_width = (bbox[2] - bbox[0]) / 2.0
    half_height = (bbox[3] - bbox[1]) / 2.0
    angle = math.radians(magnetic_declination_deg)
    sin_a = math.sin(angle)
    cos_a = math.cos(angle)
    corners = []
    for map_x in (-half_width, half_width):
        for map_y in (-half_height, half_height):
            dx = cos_a * map_x + sin_a * map_y
            dy = -sin_a * map_x + cos_a * map_y
            corners.append((center_x + dx, center_y + dy))
    return corners


def enclosing_grid_bbox(bbox: list[float], magnetic_declination_deg: float) -> list[float]:
    if abs(magnetic_declination_deg) < 0.000001:
        return bbox
    corners = oriented_bbox_corners(bbox, magnetic_declination_deg)
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    return [min(xs), min(ys), max(xs), max(ys)]


def parse_orienteering_bbox(raw: str) -> list[float]:
    bbox = parse_bbox(raw)
    validate_orienteering_bbox_size(bbox)
    return bbox


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0


def parse_date(raw: str | None) -> dt.date:
    if not raw:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("--magnetic-date must be YYYY-MM-DD") from exc


def decimal_year(date: dt.date) -> float:
    start = dt.date(date.year, 1, 1)
    end = dt.date(date.year + 1, 1, 1)
    return date.year + (date - start).days / (end - start).days


def epsg3067_to_wgs84(x: float, y: float) -> tuple[float, float]:
    flattening = 1.0 / GRS80_INV_F
    eccentricity_sq = flattening * (2.0 - flattening)
    e1 = (1.0 - math.sqrt(1.0 - eccentricity_sq)) / (1.0 + math.sqrt(1.0 - eccentricity_sq))
    mu = (y - EPSG3067_FALSE_NORTHING) / (
        GRS80_A
        * (
            1.0
            - eccentricity_sq / 4.0
            - 3.0 * eccentricity_sq * eccentricity_sq / 64.0
            - 5.0 * eccentricity_sq * eccentricity_sq * eccentricity_sq / 256.0
        )
        * EPSG3067_SCALE
    )
    phi1 = (
        mu
        + (3.0 * e1 / 2.0 - 27.0 * e1**3 / 32.0) * math.sin(2.0 * mu)
        + (21.0 * e1 * e1 / 16.0 - 55.0 * e1**4 / 32.0) * math.sin(4.0 * mu)
        + (151.0 * e1**3 / 96.0) * math.sin(6.0 * mu)
        + (1097.0 * e1**4 / 512.0) * math.sin(8.0 * mu)
    )
    n1 = GRS80_A / math.sqrt(1.0 - eccentricity_sq * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = eccentricity_sq / (1.0 - eccentricity_sq) * math.cos(phi1) ** 2
    r1 = GRS80_A * (1.0 - eccentricity_sq) / (1.0 - eccentricity_sq * math.sin(phi1) ** 2) ** 1.5
    d = (x - EPSG3067_FALSE_EASTING) / (n1 * EPSG3067_SCALE)

    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d * d / 2.0
        - (5.0 + 3.0 * t1 + 10.0 * c1 - 4.0 * c1 * c1 - 9.0 * eccentricity_sq) * d**4 / 24.0
        + (
            61.0
            + 90.0 * t1
            + 298.0 * c1
            + 45.0 * t1 * t1
            - 252.0 * eccentricity_sq
            - 3.0 * c1 * c1
        )
        * d**6
        / 720.0
    )
    lon = math.radians(EPSG3067_CENTRAL_MERIDIAN_DEG) + (
        d
        - (1.0 + 2.0 * t1 + c1) * d**3 / 6.0
        + (5.0 - 2.0 * c1 + 28.0 * t1 - 3.0 * c1 * c1 + 8.0 * eccentricity_sq + 24.0 * t1 * t1)
        * d**5
        / 120.0
    ) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lon)


def meridian_convergence_deg(latitude_deg: float, longitude_deg: float) -> float:
    lat = math.radians(latitude_deg)
    lon_delta = math.radians(longitude_deg - EPSG3067_CENTRAL_MERIDIAN_DEG)
    return math.degrees(math.atan(math.tan(lon_delta) * math.sin(lat)))


def estimate_finland_magnetic_declination_wgs84(latitude_deg: float, longitude_deg: float, date: dt.date) -> float:
    lon_offset = longitude_deg - 25.0
    lat_offset = latitude_deg - 62.0
    year_offset = decimal_year(date) - 2026.0
    # Lightweight Finland-only NEK approximation for automatic map orientation,
    # calibrated against sampled MML Erantokartta city values at the start of 2026.
    # Users can still pass an explicit value when authoritative declination matters.
    return (
        11.071507931
        + 0.432817643 * lon_offset
        + 0.378133772 * lat_offset
        + 0.20 * year_offset
    )


def estimate_finland_magnetic_declination_deg(x: float, y: float, date: dt.date) -> float:
    lat, lon = epsg3067_to_wgs84(x, y)
    return estimate_finland_magnetic_declination_wgs84(lat, lon, date)


def estimate_finland_total_correction_deg(x: float, y: float, date: dt.date) -> float:
    lat, lon = epsg3067_to_wgs84(x, y)
    magnetic_declination_deg = estimate_finland_magnetic_declination_wgs84(lat, lon, date)
    grid_to_true_correction_deg = meridian_convergence_deg(lat, lon)
    return magnetic_declination_deg + grid_to_true_correction_deg


def resolve_magnetic_declination_deg(
    raw_declination: str,
    *,
    bbox: list[float],
    magnetic_date: str | None,
) -> float:
    if raw_declination.lower() != "auto":
        try:
            return float(raw_declination)
        except ValueError as exc:
            raise ValueError("--magnetic-declination-deg must be a map-north correction in degrees or auto") from exc
    center_x, center_y = bbox_center(bbox)
    return estimate_finland_total_correction_deg(center_x, center_y, parse_date(magnetic_date))


def auth_headers(api_key: str) -> dict[str, str]:
    token = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def http_json(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={**auth_headers(api_key), "Accept": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            **auth_headers(api_key),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, api_key: str, output_path: Path) -> None:
    request = urllib.request.Request(url, headers=auth_headers(api_key))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request) as response, output_path.open("wb") as file:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            file.write(chunk)


def submit_mml_bbox_job(
    *,
    api_key: str,
    bbox: list[float],
    theme: str,
    base_url: str,
) -> str:
    process_id = "maastotietokanta_bbox"
    payload = {
        "id": process_id,
        "inputs": {
            "boundingBoxInput": bbox,
            "themeInput": theme,
            "fileFormatInput": "GPKG",
        },
    }
    response = post_json(f"{base_url}/processes/{process_id}/execution", api_key, payload)
    for link in response.get("links", []):
        if link.get("rel") == "self" and link.get("href"):
            return str(link["href"])
    job_id = response.get("jobID")
    if not job_id:
        raise RuntimeError(f"MML execution response did not include a job link: {response}")
    return f"{base_url}/jobs/{job_id}"


def wait_for_job(job_url: str, api_key: str, *, poll_seconds: float, timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    while True:
        status = http_json(job_url, api_key)
        state = str(status.get("status", "")).lower()
        if state == "successful":
            return status
        if state in {"failed", "dismissed"}:
            raise RuntimeError(f"MML job {state}: {status.get('message', status)}")
        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError(f"MML job did not finish within {timeout_seconds:.0f} seconds: {job_url}")
        time.sleep(poll_seconds)


def results_url_from_status(status: dict[str, Any], job_url: str) -> str:
    for link in status.get("links", []):
        href = link.get("href")
        if href and str(link.get("rel", "")).lower() == "self":
            return str(href)
    return job_url.rstrip("/") + "/results/"


def pick_download_url(results: dict[str, Any]) -> str:
    for result in results.get("results", []):
        if result.get("zipPath"):
            return str(result["zipPath"])
    for result in results.get("results", []):
        path = str(result.get("path", ""))
        if path.lower().endswith(".gpkg") or path.lower().endswith(".zip"):
            return path
    raise RuntimeError(f"MML results did not include a downloadable GeoPackage or zip: {results}")


def extract_first_gpkg(archive_path: Path, output_dir: Path) -> Path:
    if archive_path.suffix.lower() == ".gpkg":
        return archive_path
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        gpkg_names = [name for name in archive.namelist() if name.lower().endswith(".gpkg")]
        if not gpkg_names:
            raise RuntimeError(f"No .gpkg file found in {archive_path}")
        archive.extract(gpkg_names[0], output_dir)
        return output_dir / gpkg_names[0]


def load_table_rules(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return DEFAULT_TABLE_RULES
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError("Mapping file must be a JSON object")
    merged = dict(DEFAULT_TABLE_RULES)
    merged.update(raw)
    return merged


def convert_gpkg_to_geojson(
    gpkg_path: Path,
    *,
    bbox: list[float] | None,
    table_rules: dict[str, dict[str, Any]],
    include_unmapped: bool,
    clip_frame: OrientedFrame | None = None,
    map_frame: dict[str, Any] | None = None,
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    with sqlite3.connect(gpkg_path) as connection:
        connection.row_factory = sqlite3.Row
        for table, geometry_column in gpkg_feature_tables(connection):
            rule = table_rules.get(table)
            if rule is None and not include_unmapped:
                continue
            for row in feature_rows(connection, table, geometry_column):
                geometry = parse_gpkg_geometry(row[geometry_column])
                if geometry is None or (bbox is not None and not geometry_intersects_bbox(geometry, bbox)):
                    continue
                if clip_frame is not None:
                    geometry = clip_geometry_to_frame(geometry, clip_frame)
                    if geometry is None:
                        continue
                properties = row_properties(row, geometry_column)
                object_type, symbol = classify_feature(table, properties, geometry, rule)
                if symbol is None and not include_unmapped:
                    continue
                properties.update(
                    {
                        "source": "Maanmittauslaitos Maastotietokanta",
                        "source_table": table,
                        "symbol": symbol or table,
                        "object_type": object_type or object_type_from_geojson(geometry["type"]),
                    }
                )
                features.append({"type": "Feature", "properties": properties, "geometry": geometry})
    geojson = {
        "type": "FeatureCollection",
        "name": "mml-omap",
        "crs": {"type": "name", "properties": {"name": "EPSG:3067"}},
        "features": features,
    }
    if map_frame is not None:
        geojson["map_frame"] = map_frame
    return geojson


def gpkg_feature_tables(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT c.table_name, c.column_name
        FROM gpkg_geometry_columns c
        JOIN gpkg_contents g ON c.table_name = g.table_name
        WHERE g.data_type = 'features'
        ORDER BY c.table_name
        """
    ).fetchall()
    return [(str(row["table_name"]), str(row["column_name"])) for row in rows]


def feature_rows(connection: sqlite3.Connection, table: str, geometry_column: str) -> Any:
    quoted_table = quote_ident(table)
    quoted_geometry = quote_ident(geometry_column)
    return connection.execute(
        f"SELECT * FROM {quoted_table} WHERE {quoted_geometry} IS NOT NULL"
    )


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def row_properties(row: sqlite3.Row, geometry_column: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for key in row.keys():
        if key == geometry_column:
            continue
        value = row[key]
        if isinstance(value, bytes):
            continue
        properties[key] = value
    return properties


def classify_feature(
    table: str,
    properties: dict[str, Any],
    geometry: dict[str, Any],
    rule: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    object_type = object_type_from_geojson(geometry["type"])
    if rule is None:
        return object_type, None
    symbol = rule.get("symbol")
    kohdeluokka_rules = rule.get("kohdeluokka")
    kohdeluokka = properties.get("kohdeluokka")
    if isinstance(kohdeluokka_rules, dict) and kohdeluokka is not None:
        symbol = kohdeluokka_rules.get(str(kohdeluokka), symbol)
    return str(rule.get("object_type", object_type)), str(symbol) if symbol else None


def object_type_from_geojson(geometry_type: str) -> str:
    if geometry_type in {"Point", "MultiPoint"}:
        return "point"
    if geometry_type in {"Polygon", "MultiPolygon"}:
        return "area"
    return "line"


def parse_gpkg_geometry(raw: bytes | memoryview | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    data = bytes(raw)
    if len(data) < 8 or data[:2] != b"GP":
        return None
    flags = data[3]
    envelope_code = (flags >> 1) & 0b111
    wkb_offset = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(envelope_code, 0)
    return parse_wkb(data[wkb_offset:])


def parse_wkb(data: bytes) -> dict[str, Any]:
    geometry, offset = read_wkb_geometry(data, 0)
    if offset > len(data):
        raise ValueError("Invalid WKB geometry")
    return geometry


def read_wkb_geometry(data: bytes, offset: int) -> tuple[dict[str, Any], int]:
    endian_flag = data[offset]
    endian = "<" if endian_flag == 1 else ">"
    geometry_type = struct.unpack_from(endian + "I", data, offset + 1)[0]
    offset += 5
    base_type, dimensions = normalize_wkb_type(geometry_type)
    if base_type == 1:
        point, offset = read_wkb_point(data, offset, endian, dimensions)
        return {"type": "Point", "coordinates": point}, offset
    if base_type == 2:
        count = struct.unpack_from(endian + "I", data, offset)[0]
        offset += 4
        coordinates, offset = read_wkb_points(data, offset, endian, count, dimensions)
        return {"type": "LineString", "coordinates": coordinates}, offset
    if base_type == 3:
        ring_count = struct.unpack_from(endian + "I", data, offset)[0]
        offset += 4
        rings = []
        for _ in range(ring_count):
            count = struct.unpack_from(endian + "I", data, offset)[0]
            offset += 4
            ring, offset = read_wkb_points(data, offset, endian, count, dimensions)
            rings.append(ring)
        return {"type": "Polygon", "coordinates": rings}, offset
    if base_type in {4, 5, 6}:
        count = struct.unpack_from(endian + "I", data, offset)[0]
        offset += 4
        geometries = []
        for _ in range(count):
            geometry, offset = read_wkb_geometry(data, offset)
            geometries.append(geometry["coordinates"])
        names = {4: "MultiPoint", 5: "MultiLineString", 6: "MultiPolygon"}
        return {"type": names[base_type], "coordinates": geometries}, offset
    raise ValueError(f"Unsupported WKB geometry type: {geometry_type}")


def normalize_wkb_type(geometry_type: int) -> tuple[int, int]:
    # ISO WKB uses +1000/+2000/+3000 for Z/M/ZM variants.
    if geometry_type >= 3000:
        return geometry_type - 3000, 4
    if geometry_type >= 2000:
        return geometry_type - 2000, 3
    if geometry_type >= 1000:
        return geometry_type - 1000, 3
    return geometry_type, 2


def read_wkb_point(data: bytes, offset: int, endian: str, dimensions: int) -> tuple[list[float], int]:
    values = struct.unpack_from(endian + ("d" * dimensions), data, offset)
    return [values[0], values[1]], offset + dimensions * 8


def read_wkb_points(
    data: bytes,
    offset: int,
    endian: str,
    count: int,
    dimensions: int,
) -> tuple[list[list[float]], int]:
    coordinates = []
    for _ in range(count):
        point, offset = read_wkb_point(data, offset, endian, dimensions)
        coordinates.append(point)
    return coordinates, offset


def geometry_intersects_bbox(geometry: dict[str, Any], bbox: list[float]) -> bool:
    xs: list[float] = []
    ys: list[float] = []
    collect_xy(geometry["coordinates"], xs, ys)
    if not xs or not ys:
        return False
    return max(xs) >= bbox[0] and min(xs) <= bbox[2] and max(ys) >= bbox[1] and min(ys) <= bbox[3]


class OrientedFrame:
    def __init__(self, bbox: list[float], magnetic_declination_deg: float) -> None:
        self.bbox = bbox
        self.center_x, self.center_y = bbox_center(bbox)
        self.half_width = (bbox[2] - bbox[0]) / 2.0
        self.half_height = (bbox[3] - bbox[1]) / 2.0
        angle = math.radians(magnetic_declination_deg)
        self.sin_a = math.sin(angle)
        self.cos_a = math.cos(angle)

    def to_local(self, coordinate: Any) -> tuple[float, float]:
        dx = float(coordinate[0]) - self.center_x
        dy = float(coordinate[1]) - self.center_y
        return self.cos_a * dx - self.sin_a * dy, self.sin_a * dx + self.cos_a * dy

    def to_grid(self, point: tuple[float, float]) -> list[float]:
        x, y = point
        dx = self.cos_a * x + self.sin_a * y
        dy = -self.sin_a * x + self.cos_a * y
        return [self.center_x + dx, self.center_y + dy]

    def contains_local(self, point: tuple[float, float]) -> bool:
        x, y = point
        return -self.half_width <= x <= self.half_width and -self.half_height <= y <= self.half_height


def clip_line_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    frame: OrientedFrame,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    p = [-dx, dx, -dy, dy]
    q = [
        a[0] + frame.half_width,
        frame.half_width - a[0],
        a[1] + frame.half_height,
        frame.half_height - a[1],
    ]
    u1 = 0.0
    u2 = 1.0
    for p_i, q_i in zip(p, q):
        if abs(p_i) < 1e-12:
            if q_i < 0.0:
                return None
            continue
        ratio = q_i / p_i
        if p_i < 0.0:
            u1 = max(u1, ratio)
        else:
            u2 = min(u2, ratio)
        if u1 > u2:
            return None
    return (a[0] + u1 * dx, a[1] + u1 * dy), (a[0] + u2 * dx, a[1] + u2 * dy)


def clip_line_string(coordinates: list[Any], frame: OrientedFrame) -> list[list[list[float]]]:
    if len(coordinates) < 2:
        return []
    local_points = [frame.to_local(point) for point in coordinates]
    lines: list[list[list[float]]] = []
    current: list[list[float]] = []
    for a, b in zip(local_points, local_points[1:]):
        clipped = clip_line_segment(a, b, frame)
        if clipped is None:
            if len(current) > 1:
                lines.append(current)
            current = []
            continue
        start, end = clipped
        start_grid = frame.to_grid(start)
        end_grid = frame.to_grid(end)
        if not current:
            current = [start_grid, end_grid]
        elif points_equal(current[-1], start_grid):
            current.append(end_grid)
        else:
            if len(current) > 1:
                lines.append(current)
            current = [start_grid, end_grid]
    if len(current) > 1:
        lines.append(current)
    return lines


def points_equal(a: list[float], b: list[float]) -> bool:
    return abs(a[0] - b[0]) < 1e-7 and abs(a[1] - b[1]) < 1e-7


def clip_polygon_ring_local(ring: list[tuple[float, float]], frame: OrientedFrame) -> list[tuple[float, float]]:
    def clip_edge(
        points: list[tuple[float, float]],
        inside: Any,
        intersect: Any,
    ) -> list[tuple[float, float]]:
        if not points:
            return []
        output: list[tuple[float, float]] = []
        previous = points[-1]
        previous_inside = inside(previous)
        for current in points:
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersect(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersect(previous, current))
            previous = current
            previous_inside = current_inside
        return output

    def vertical(x_limit: float, point: tuple[float, float]) -> bool:
        return point[0] >= x_limit if x_limit < 0 else point[0] <= x_limit

    def horizontal(y_limit: float, point: tuple[float, float]) -> bool:
        return point[1] >= y_limit if y_limit < 0 else point[1] <= y_limit

    def intersect_x(x_limit: float, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        if abs(b[0] - a[0]) < 1e-12:
            return x_limit, a[1]
        t = (x_limit - a[0]) / (b[0] - a[0])
        return x_limit, a[1] + t * (b[1] - a[1])

    def intersect_y(y_limit: float, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        if abs(b[1] - a[1]) < 1e-12:
            return a[0], y_limit
        t = (y_limit - a[1]) / (b[1] - a[1])
        return a[0] + t * (b[0] - a[0]), y_limit

    clipped = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    clipped = clip_edge(
        clipped,
        lambda point: vertical(-frame.half_width, point),
        lambda a, b: intersect_x(-frame.half_width, a, b),
    )
    clipped = clip_edge(
        clipped,
        lambda point: vertical(frame.half_width, point),
        lambda a, b: intersect_x(frame.half_width, a, b),
    )
    clipped = clip_edge(
        clipped,
        lambda point: horizontal(-frame.half_height, point),
        lambda a, b: intersect_y(-frame.half_height, a, b),
    )
    clipped = clip_edge(
        clipped,
        lambda point: horizontal(frame.half_height, point),
        lambda a, b: intersect_y(frame.half_height, a, b),
    )
    if clipped and clipped[0] != clipped[-1]:
        clipped.append(clipped[0])
    return clipped


def clip_polygon(coordinates: list[Any], frame: OrientedFrame) -> list[list[list[float]]] | None:
    rings: list[list[list[float]]] = []
    for ring in coordinates:
        if len(ring) < 4:
            continue
        local_ring = [frame.to_local(point) for point in ring]
        clipped_ring = clip_polygon_ring_local(local_ring, frame)
        if len(clipped_ring) >= 4:
            rings.append([frame.to_grid(point) for point in clipped_ring])
    if not rings:
        return None
    return rings


def clip_geometry_to_frame(geometry: dict[str, Any], frame: OrientedFrame) -> dict[str, Any] | None:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        point = frame.to_local(coordinates)
        return geometry if frame.contains_local(point) else None
    if geometry_type == "MultiPoint":
        points = [point for point in coordinates or [] if frame.contains_local(frame.to_local(point))]
        return {"type": "MultiPoint", "coordinates": points} if points else None
    if geometry_type == "LineString":
        lines = clip_line_string(coordinates or [], frame)
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}
    if geometry_type == "MultiLineString":
        lines = []
        for line in coordinates or []:
            lines.extend(clip_line_string(line, frame))
        return {"type": "MultiLineString", "coordinates": lines} if lines else None
    if geometry_type == "Polygon":
        polygon = clip_polygon(coordinates or [], frame)
        return {"type": "Polygon", "coordinates": polygon} if polygon else None
    if geometry_type == "MultiPolygon":
        polygons = []
        for polygon in coordinates or []:
            clipped_polygon = clip_polygon(polygon, frame)
            if clipped_polygon:
                polygons.append(clipped_polygon)
        return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None
    return geometry


def clip_geojson_to_frame(geojson: dict[str, Any], frame: OrientedFrame) -> dict[str, Any]:
    features = []
    for feature in geojson_features(geojson):
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        clipped_geometry = clip_geometry_to_frame(geometry, frame)
        if clipped_geometry is None:
            continue
        clipped_feature = dict(feature)
        clipped_feature["geometry"] = clipped_geometry
        features.append(clipped_feature)
    output = dict(geojson)
    output["type"] = "FeatureCollection"
    output["features"] = features
    return output


def collect_xy(raw: Any, xs: list[float], ys: list[float]) -> None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2 and all(isinstance(v, (int, float)) for v in raw[:2]):
        xs.append(float(raw[0]))
        ys.append(float(raw[1]))
        return
    if isinstance(raw, (list, tuple)):
        for item in raw:
            collect_xy(item, xs, ys)


def geojson_features(geojson: dict[str, Any]) -> list[dict[str, Any]]:
    if geojson.get("type") == "FeatureCollection":
        features = geojson.get("features")
        if not isinstance(features, list):
            raise ValueError("FeatureCollection.features must be an array")
        return [feature for feature in features if isinstance(feature, dict)]
    if geojson.get("type") == "Feature":
        return [geojson]
    raise ValueError("Input must be a GeoJSON FeatureCollection or Feature")


def geojson_bbox(geojson: dict[str, Any], raw_bbox: str | None = None) -> list[float]:
    if raw_bbox:
        return parse_bbox(raw_bbox)
    map_frame = geojson.get("map_frame")
    if isinstance(map_frame, dict) and isinstance(map_frame.get("bbox"), list):
        return parse_bbox(",".join(str(value) for value in map_frame["bbox"]))
    xs: list[float] = []
    ys: list[float] = []
    for feature in geojson_features(geojson):
        geometry = feature.get("geometry") or {}
        collect_xy(geometry.get("coordinates"), xs, ys)
    if not xs or not ys:
        raise ValueError("GeoJSON contains no coordinates and --bbox was not provided")
    return [min(xs), min(ys), max(xs), max(ys)]


def geojson_map_frame_declination(geojson: dict[str, Any]) -> float | None:
    map_frame = geojson.get("map_frame")
    if not isinstance(map_frame, dict):
        return None
    declination = map_frame.get("magnetic_declination_deg")
    if isinstance(declination, (int, float)):
        return float(declination)
    return None


class RenderTransform:
    def __init__(
        self,
        bbox: list[float],
        scale: int,
        margin_mm: float,
        magnetic_declination_deg: float = 0.0,
    ) -> None:
        self.min_x, self.min_y, self.max_x, self.max_y = bbox
        self.scale = max(int(scale), 1)
        self.margin_mm = max(float(margin_mm), 0.0)
        self.width_m = max(self.max_x - self.min_x, 0.001)
        self.height_m = max(self.max_y - self.min_y, 0.001)
        self.center_x = (self.min_x + self.max_x) / 2.0
        self.center_y = (self.min_y + self.max_y) / 2.0
        self.magnetic_declination_deg = float(magnetic_declination_deg)
        angle = math.radians(self.magnetic_declination_deg)
        self._sin_declination = math.sin(angle)
        self._cos_declination = math.cos(angle)
        self.map_width_mm = self.width_m * 1000.0 / self.scale
        self.map_height_mm = self.height_m * 1000.0 / self.scale
        self.page_width_mm = self.map_width_mm + self.margin_mm * 2.0
        self.page_height_mm = self.map_height_mm + self.margin_mm * 2.0

    def to_mm(self, coordinate: Any) -> tuple[float, float]:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            raise ValueError(f"Invalid coordinate: {coordinate!r}")
        dx = float(coordinate[0]) - self.center_x
        dy = float(coordinate[1]) - self.center_y
        map_x = self._cos_declination * dx - self._sin_declination * dy
        map_y = self._sin_declination * dx + self._cos_declination * dy
        x = self.margin_mm + (self.width_m / 2.0 + map_x) * 1000.0 / self.scale
        y = self.margin_mm + (self.height_m / 2.0 - map_y) * 1000.0 / self.scale
        return x, y


def feature_symbol(feature: dict[str, Any]) -> str:
    properties = feature.get("properties") or {}
    return str(properties.get("symbol", properties.get("source_table", "unknown")))


def feature_style(feature: dict[str, Any]) -> dict[str, Any]:
    return SYMBOL_STYLES.get(feature_symbol(feature), DEFAULT_STYLE)


def iter_geometry_parts(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type in {"Point", "LineString", "Polygon"}:
        return [{"type": geometry_type, "coordinates": coordinates}]
    if geometry_type == "MultiPoint":
        return [{"type": "Point", "coordinates": point} for point in coordinates or []]
    if geometry_type == "MultiLineString":
        return [{"type": "LineString", "coordinates": line} for line in coordinates or []]
    if geometry_type == "MultiPolygon":
        return [{"type": "Polygon", "coordinates": polygon} for polygon in coordinates or []]
    return []


def svg_path_for_ring(ring: list[Any], transform: RenderTransform) -> str:
    if not ring:
        return ""
    commands = []
    first_x, first_y = transform.to_mm(ring[0])
    commands.append(f"M {first_x:.3f} {first_y:.3f}")
    for coordinate in ring[1:]:
        x, y = transform.to_mm(coordinate)
        commands.append(f"L {x:.3f} {y:.3f}")
    commands.append("Z")
    return " ".join(commands)


def svg_path_for_line(line: list[Any], transform: RenderTransform) -> str:
    if not line:
        return ""
    first_x, first_y = transform.to_mm(line[0])
    commands = [f"M {first_x:.3f} {first_y:.3f}"]
    for coordinate in line[1:]:
        x, y = transform.to_mm(coordinate)
        commands.append(f"L {x:.3f} {y:.3f}")
    return " ".join(commands)


def render_svg(geojson: dict[str, Any], output_path: Path, *, transform: RenderTransform) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{transform.page_width_mm:.3f}mm" height="{transform.page_height_mm:.3f}mm" '
            f'viewBox="0 0 {transform.page_width_mm:.3f} {transform.page_height_mm:.3f}">'
        ),
        '<rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/>',
    ]
    for feature in geojson_features(geojson):
        style = feature_style(feature)
        stroke = style.get("stroke", "none")
        fill = style.get("fill", "none")
        stroke_width = float(style.get("stroke_width_mm", 0.18))
        dasharray = style.get("dasharray")
        dash_attr = f' stroke-dasharray="{dasharray}"' if dasharray else ""
        for part in iter_geometry_parts(feature.get("geometry") or {}):
            if part["type"] == "Polygon":
                path = " ".join(svg_path_for_ring(ring, transform) for ring in part.get("coordinates") or [])
                lines.append(
                    f'<path d="{path}" stroke="{stroke}" fill="{fill}" '
                    f'stroke-width="{stroke_width:.3f}" fill-rule="evenodd"{dash_attr}/>'
                )
            elif part["type"] == "LineString":
                path = svg_path_for_line(part.get("coordinates") or [], transform)
                lines.append(
                    f'<path d="{path}" stroke="{stroke}" fill="none" '
                    f'stroke-width="{stroke_width:.3f}" stroke-linecap="round" '
                    f'stroke-linejoin="round"{dash_attr}/>'
                )
            elif part["type"] == "Point":
                x, y = transform.to_mm(part.get("coordinates"))
                radius = max(stroke_width * 2.0, 0.35)
                point_fill = fill if fill != "none" else stroke
                lines.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{radius:.3f}" fill="{point_fill}"/>')
    lines.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def color_to_rgb(raw: str) -> tuple[int, int, int] | None:
    if raw == "none":
        return None
    value = raw.lstrip("#")
    if len(value) != 6:
        return None
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def make_canvas(width: int, height: int) -> bytearray:
    return bytearray([255, 255, 255, 255]) * width * height


def set_pixel(canvas: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    index = (y * width + x) * 4
    canvas[index:index + 4] = bytes([color[0], color[1], color[2], 255])


def draw_line(
    canvas: bytearray,
    width: int,
    height: int,
    a: tuple[int, int],
    b: tuple[int, int],
    color: tuple[int, int, int],
    stroke_px: int,
) -> None:
    x0, y0 = a
    x1, y1 = b
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    radius = max(stroke_px // 2, 0)
    while True:
        for oy in range(-radius, radius + 1):
            for ox in range(-radius, radius + 1):
                set_pixel(canvas, width, height, x0 + ox, y0 + oy, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def point_in_polygon(x: float, y: float, ring: list[tuple[int, int]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i, point in enumerate(ring):
        xi, yi = point
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / max(yj - yi, 0.000001) + xi:
            inside = not inside
        j = i
    return inside


def fill_polygon(
    canvas: bytearray,
    width: int,
    height: int,
    rings: list[list[tuple[int, int]]],
    color: tuple[int, int, int],
) -> None:
    if not rings or not rings[0]:
        return
    xs = [point[0] for ring in rings for point in ring]
    ys = [point[1] for ring in rings for point in ring]
    min_x = max(min(xs), 0)
    max_x = min(max(xs), width - 1)
    min_y = max(min(ys), 0)
    max_y = min(max(ys), height - 1)
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if point_in_polygon(x + 0.5, y + 0.5, rings[0]) and not any(
                point_in_polygon(x + 0.5, y + 0.5, hole) for hole in rings[1:]
            ):
                set_pixel(canvas, width, height, x, y, color)


def draw_circle(
    canvas: bytearray,
    width: int,
    height: int,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int],
) -> None:
    cx, cy = center
    radius = max(radius, 1)
    r2 = radius * radius
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) * (x - cx) + (y - cy) * (y - cy) <= r2:
                set_pixel(canvas, width, height, x, y, color)


def write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * stride:(y + 1) * stride])

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + kind
            + data
            + zlib.crc32(kind + data).to_bytes(4, "big")
        )

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 6, 0, 0, 0])))
    png.extend(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
    png.extend(chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(png))


def render_png(geojson: dict[str, Any], output_path: Path, *, transform: RenderTransform, dpi: int) -> None:
    width = max(1, int(round(transform.page_width_mm / 25.4 * dpi)))
    height = max(1, int(round(transform.page_height_mm / 25.4 * dpi)))
    px_per_mm = dpi / 25.4
    canvas = make_canvas(width, height)

    def to_px(coordinate: Any) -> tuple[int, int]:
        x, y = transform.to_mm(coordinate)
        return int(round(x * px_per_mm)), int(round(y * px_per_mm))

    for feature in geojson_features(geojson):
        style = feature_style(feature)
        stroke = color_to_rgb(str(style.get("stroke", "none")))
        fill = color_to_rgb(str(style.get("fill", "none")))
        stroke_px = max(1, int(round(float(style.get("stroke_width_mm", 0.18)) * px_per_mm)))
        for part in iter_geometry_parts(feature.get("geometry") or {}):
            if part["type"] == "Polygon":
                rings = [[to_px(point) for point in ring] for ring in part.get("coordinates") or []]
                if fill:
                    fill_polygon(canvas, width, height, rings, fill)
                if stroke:
                    for ring in rings:
                        for a, b in zip(ring, ring[1:] + ring[:1]):
                            draw_line(canvas, width, height, a, b, stroke, stroke_px)
            elif part["type"] == "LineString" and stroke:
                points = [to_px(point) for point in part.get("coordinates") or []]
                for a, b in zip(points, points[1:]):
                    draw_line(canvas, width, height, a, b, stroke, stroke_px)
            elif part["type"] == "Point":
                color = fill or stroke or (0, 0, 0)
                draw_circle(canvas, width, height, to_px(part.get("coordinates")), max(2, stroke_px * 2), color)
    write_png(output_path, width, height, canvas)


def pdf_color_operator(color: tuple[int, int, int], stroke: bool) -> str:
    values = " ".join(f"{channel / 255.0:.4f}" for channel in color)
    return values + (" RG" if stroke else " rg")


def pdf_point(transform: RenderTransform, coordinate: Any) -> tuple[float, float]:
    x_mm, y_mm = transform.to_mm(coordinate)
    scale = 72.0 / 25.4
    return x_mm * scale, (transform.page_height_mm - y_mm) * scale


def render_pdf(geojson: dict[str, Any], output_path: Path, *, transform: RenderTransform) -> None:
    page_width = transform.page_width_mm * 72.0 / 25.4
    page_height = transform.page_height_mm * 72.0 / 25.4
    commands = ["1 1 1 rg", f"0 0 {page_width:.3f} {page_height:.3f} re", "f"]
    for feature in geojson_features(geojson):
        style = feature_style(feature)
        stroke = color_to_rgb(str(style.get("stroke", "none")))
        fill = color_to_rgb(str(style.get("fill", "none")))
        stroke_width = float(style.get("stroke_width_mm", 0.18)) * 72.0 / 25.4
        for part in iter_geometry_parts(feature.get("geometry") or {}):
            if part["type"] == "Polygon":
                if fill:
                    commands.append(pdf_color_operator(fill, stroke=False))
                if stroke:
                    commands.append(pdf_color_operator(stroke, stroke=True))
                    commands.append(f"{stroke_width:.3f} w")
                for ring in part.get("coordinates") or []:
                    if not ring:
                        continue
                    x, y = pdf_point(transform, ring[0])
                    commands.append(f"{x:.3f} {y:.3f} m")
                    for coordinate in ring[1:]:
                        x, y = pdf_point(transform, coordinate)
                        commands.append(f"{x:.3f} {y:.3f} l")
                    commands.append("h")
                commands.append("B" if fill and stroke else ("f" if fill else "S"))
            elif part["type"] == "LineString" and stroke:
                line = part.get("coordinates") or []
                if len(line) < 2:
                    continue
                commands.append(pdf_color_operator(stroke, stroke=True))
                commands.append(f"{stroke_width:.3f} w")
                x, y = pdf_point(transform, line[0])
                commands.append(f"{x:.3f} {y:.3f} m")
                for coordinate in line[1:]:
                    x, y = pdf_point(transform, coordinate)
                    commands.append(f"{x:.3f} {y:.3f} l")
                commands.append("S")
            elif part["type"] == "Point":
                color = fill or stroke or (0, 0, 0)
                commands.append(pdf_color_operator(color, stroke=False))
                x, y = pdf_point(transform, part.get("coordinates"))
                radius = max(stroke_width * 2.0, 1.0)
                commands.append(f"{x - radius:.3f} {y - radius:.3f} {radius * 2:.3f} {radius * 2:.3f} re")
                commands.append("f")
    content = ("\n".join(commands) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width:.3f} {page_height:.3f}] "
            f"/Contents 4 0 R >>"
        ).encode("ascii"),
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"endstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(output))


def command_download(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get(args.api_key_env)
    if not api_key:
        raise ValueError(f"Provide --api-key or set ${args.api_key_env}")
    bbox = parse_orienteering_bbox(args.bbox)
    magnetic_declination_deg = resolve_magnetic_declination_deg(
        args.magnetic_declination_deg,
        bbox=bbox,
        magnetic_date=args.magnetic_date,
    )
    mml_bbox = enclosing_grid_bbox(bbox, magnetic_declination_deg)
    job_url = submit_mml_bbox_job(
        api_key=api_key,
        bbox=mml_bbox,
        theme=args.theme,
        base_url=args.base_url.rstrip("/"),
    )
    status = wait_for_job(
        job_url,
        api_key,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    results = http_json(results_url_from_status(status, job_url), api_key)
    download_url = pick_download_url(results)
    download_file(download_url, api_key, Path(args.output))
    return 0


def command_convert_gpkg(args: argparse.Namespace) -> int:
    paper_bbox = parse_orienteering_bbox(args.bbox) if args.bbox else None
    magnetic_date = parse_date(args.magnetic_date)
    magnetic_declination_deg = (
        resolve_magnetic_declination_deg(
            args.magnetic_declination_deg,
            bbox=paper_bbox,
            magnetic_date=magnetic_date.isoformat(),
        )
        if paper_bbox
        else 0.0
    )
    bbox = enclosing_grid_bbox(paper_bbox, magnetic_declination_deg) if paper_bbox else None
    clip_frame = OrientedFrame(paper_bbox, magnetic_declination_deg) if paper_bbox else None
    rules = load_table_rules(Path(args.mapping) if args.mapping else None)
    geojson = convert_gpkg_to_geojson(
        Path(args.input),
        bbox=bbox,
        clip_frame=clip_frame,
        table_rules=rules,
        include_unmapped=args.include_unmapped,
        map_frame=(
            {
                "bbox": paper_bbox,
                "magnetic_declination_deg": magnetic_declination_deg,
                "magnetic_date": magnetic_date.isoformat(),
            }
            if paper_bbox
            else None
        ),
    )
    write_json(Path(args.output), geojson)
    print(f"Wrote {len(geojson['features'])} features.")
    return 0


def command_generate(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get(args.api_key_env)
    if not api_key:
        raise ValueError(f"Provide --api-key or set ${args.api_key_env}")
    output = Path(args.output)
    work_dir = Path(args.work_dir)
    archive_path = work_dir / (output.stem + ".zip")
    download_args = argparse.Namespace(**vars(args), output=str(archive_path))
    command_download(download_args)
    gpkg_path = extract_first_gpkg(archive_path, work_dir / output.stem)
    rules = load_table_rules(Path(args.mapping) if args.mapping else None)
    paper_bbox = parse_orienteering_bbox(args.bbox)
    magnetic_date = parse_date(args.magnetic_date)
    magnetic_declination_deg = resolve_magnetic_declination_deg(
        args.magnetic_declination_deg,
        bbox=paper_bbox,
        magnetic_date=magnetic_date.isoformat(),
    )
    geojson = convert_gpkg_to_geojson(
        gpkg_path,
        bbox=enclosing_grid_bbox(paper_bbox, magnetic_declination_deg),
        clip_frame=OrientedFrame(paper_bbox, magnetic_declination_deg),
        table_rules=rules,
        include_unmapped=args.include_unmapped,
        map_frame={
            "bbox": paper_bbox,
            "magnetic_declination_deg": magnetic_declination_deg,
            "magnetic_date": magnetic_date.isoformat(),
        },
    )
    write_json(output, geojson)
    print(f"Wrote {len(geojson['features'])} features from {gpkg_path}.")
    return 0


def make_render_transform(args: argparse.Namespace, geojson: dict[str, Any]) -> RenderTransform:
    bbox = geojson_bbox(geojson, args.bbox)
    if args.bbox:
        validate_orienteering_bbox_size(bbox)
    metadata_declination = None if args.bbox else geojson_map_frame_declination(geojson)
    if args.magnetic_declination_deg.lower() == "auto" and metadata_declination is not None:
        magnetic_declination_deg = metadata_declination
    else:
        magnetic_declination_deg = resolve_magnetic_declination_deg(
            args.magnetic_declination_deg,
            bbox=bbox,
            magnetic_date=args.magnetic_date,
        )
    return RenderTransform(
        bbox,
        scale=args.scale,
        margin_mm=args.margin_mm,
        magnetic_declination_deg=magnetic_declination_deg,
    )


def clip_geojson_for_render(args: argparse.Namespace, geojson: dict[str, Any], transform: RenderTransform) -> dict[str, Any]:
    if not args.bbox and not isinstance(geojson.get("map_frame"), dict):
        return geojson
    bbox = parse_bbox(args.bbox) if args.bbox else geojson_bbox(geojson)
    return clip_geojson_to_frame(geojson, OrientedFrame(bbox, transform.magnetic_declination_deg))


def command_render_svg(args: argparse.Namespace) -> int:
    geojson = read_json(Path(args.input))
    transform = make_render_transform(args, geojson)
    render_svg(clip_geojson_for_render(args, geojson, transform), Path(args.output), transform=transform)
    return 0


def command_render_png(args: argparse.Namespace) -> int:
    geojson = read_json(Path(args.input))
    transform = make_render_transform(args, geojson)
    render_png(clip_geojson_for_render(args, geojson, transform), Path(args.output), transform=transform, dpi=args.dpi)
    return 0


def command_render_pdf(args: argparse.Namespace) -> int:
    geojson = read_json(Path(args.input))
    transform = make_render_transform(args, geojson)
    render_pdf(clip_geojson_for_render(args, geojson, transform), Path(args.output), transform=transform)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and render GeoJSON from Maanmittauslaitos open data.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common_api = argparse.ArgumentParser(add_help=False)
    common_api.add_argument("--api-key", help="MML API key. Prefer $MML_API_KEY for shell history safety.")
    common_api.add_argument("--api-key-env", default="MML_API_KEY")
    common_api.add_argument("--base-url", default=MML_OGC_PROCESSES_URL)
    common_api.add_argument("--poll-seconds", type=float, default=5.0)
    common_api.add_argument("--timeout-seconds", type=float, default=600.0)

    download = subparsers.add_parser("download", parents=[common_api], help="Download MML bbox GeoPackage zip.")
    download.add_argument("output")
    download.add_argument("--bbox", required=True, help="min_x,min_y,max_x,max_y in EPSG:3067 meters")
    download.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK. Expands the MML fetch bbox.",
    )
    download.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")
    download.add_argument("--theme", default="maastotietokanta_kaikki")
    download.set_defaults(func=command_download)

    convert = subparsers.add_parser("convert-gpkg", help="Convert an existing MML GeoPackage to symbolized GeoJSON.")
    convert.add_argument("input")
    convert.add_argument("output")
    convert.add_argument("--bbox", help="Optional min_x,min_y,max_x,max_y clip in EPSG:3067 meters")
    convert.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK. Used when --bbox is set.",
    )
    convert.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")
    convert.add_argument("--mapping", help="JSON table mapping overrides")
    convert.add_argument("--include-unmapped", action="store_true")
    convert.set_defaults(func=command_convert_gpkg)

    generate = subparsers.add_parser(
        "generate",
        parents=[common_api],
        help="Download MML bbox data and write symbolized GeoJSON.",
    )
    generate.add_argument("output")
    generate.add_argument("--bbox", required=True, help="min_x,min_y,max_x,max_y in EPSG:3067 meters")
    generate.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK. Expands the MML fetch bbox.",
    )
    generate.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")
    generate.add_argument("--theme", default="maastotietokanta_kaikki")
    generate.add_argument("--work-dir", default="builds/mml_downloads")
    generate.add_argument("--mapping", help="JSON table mapping overrides")
    generate.add_argument("--include-unmapped", action="store_true")
    generate.set_defaults(func=command_generate)

    common_render = argparse.ArgumentParser(add_help=False)
    common_render.add_argument("input", help="Input GeoJSON")
    common_render.add_argument("output")
    common_render.add_argument("--bbox", help="Optional render bounds as min_x,min_y,max_x,max_y")
    common_render.add_argument("--scale", type=int, default=10000)
    common_render.add_argument("--margin-mm", type=float, default=5.0)
    common_render.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK. Rotates the rendered map frame.",
    )
    common_render.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")

    render_svg_parser = subparsers.add_parser("render-svg", parents=[common_render], help="Render GeoJSON to SVG.")
    render_svg_parser.set_defaults(func=command_render_svg)

    render_png_parser = subparsers.add_parser("render-png", parents=[common_render], help="Render GeoJSON to PNG.")
    render_png_parser.add_argument("--dpi", type=int, default=300)
    render_png_parser.set_defaults(func=command_render_png)

    render_pdf_parser = subparsers.add_parser("render-pdf", parents=[common_render], help="Render GeoJSON to PDF.")
    render_pdf_parser.set_defaults(func=command_render_pdf)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, sqlite3.Error, urllib.error.URLError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
