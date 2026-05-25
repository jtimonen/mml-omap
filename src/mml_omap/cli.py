"""Command line interface for exporting MML open data to GeoJSON.

The primary output is GeoJSON in EPSG:3067 coordinates. When the default mapping
is enabled, features also get `symbol` and `object_type` properties.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import math
import os
import sqlite3
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path
from typing import Any

import certifi

from .symbols import (
    DEFAULT_STYLE,
    IOF_NUMBER_TO_RENDER_SYMBOL,
    SYMBOL_RENDER_ORDER,
    SYMBOL_STYLES,
    export_symbol_library,
    iof_symbol_metadata as symbol_library_metadata,
)


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
            # Roads and streets, grouped by render symbol.
            "12111": "major_road",
            "12112": "major_road",
            "12121": "major_road",
            "12122": "major_road",
            "12131": "major_road",
            "12132": "major_road",
            "12141": "road",
            "12142": "road",
            "12151": None,
            "12152": None,
            # Tracks, paths, footways.
            "12311": "small_path",
            "12312": "small_path",
            "12313": "path",
            "12314": "road",
            "12315": "small_path",
            "12316": "small_path",
            "12317": "small_path",
        },
    },
    "rautatie": {"object_type": "line", "symbol": "railway"},
    "aita": {"object_type": "line", "symbol": "fence"},
    "jyrkanne": {"object_type": "line", "symbol": "cliff"},
    "virtavesikapea": {
        "object_type": "line",
        "symbol": "stream",
        "kohdeluokka": {
            "36312": "wide_stream",
        },
    },
    "rakennusreunaviiva": {"object_type": "line", "symbol": "building"},
    "jarvi": {"object_type": "area", "symbol": "lake"},
    "meri": {"object_type": "area", "symbol": "lake"},
    "virtavesialue": {"object_type": "area", "symbol": "river"},
    "suo": {"object_type": "area", "symbol": "swamp"},
    "soistuma": {"object_type": "area", "symbol": "swamp"},
    "maatalousmaa": {"object_type": "area", "symbol": "cultivated_land"},
    "niitty": {"object_type": "area", "symbol": "field"},
    "muuavoinalue": {"object_type": "area", "symbol": "field"},
    "puisto": {"object_type": "area", "symbol": "field"},
    "urheilujavirkistysalue": {"object_type": "area", "symbol": "recreation_area"},
    "kallioalue": {"object_type": "area", "symbol": "open_rock"},
    "rakennus": {"object_type": "area", "symbol": "building"},
    "kivi": {"object_type": "point", "symbol": "mapped_rock"},
    "paikannimi": {
        "object_type": "point",
        "symbol": "place_label",
        "kohdeluokka": {
            "35010": "water_label",
            "35030": "water_label",
        },
    },
    "taajaanrakennettualue": {"object_type": "area", "symbol": "private_yard"},
}

DEFAULT_NORTH_LINE_SPACING_M = 300.0
DEFAULT_CONTOUR_MERGE_TOLERANCE_M = 20.0
DEFAULT_TERRAIN_CONTEXT_MARGIN_M = 150.0
MML_LASER_MAP_SHEET_GRID_M = 3000.0
DEFAULT_MML_JOB_ATTEMPTS = 3


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


PROGRESS_ENABLED = True


def progress(message: str) -> None:
    if not PROGRESS_ENABLED:
        return
    print(message, file=sys.stderr, flush=True)


def read_env_file_value(name: str, path: Path = Path(".env")) -> str | None:
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


def resolve_api_key(args: argparse.Namespace) -> str:
    api_key = args.api_key or os.environ.get(args.api_key_env) or read_env_file_value(args.api_key_env)
    if not api_key:
        raise ValueError(f"Provide --api-key, set ${args.api_key_env}, or add {args.api_key_env}=... to .env")
    return api_key


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


def format_bbox(bbox: list[float]) -> str:
    return ",".join(f"{value:.1f}" for value in bbox)


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0


def expand_bbox(bbox: list[float], margin_m: float) -> list[float]:
    return [bbox[0] - margin_m, bbox[1] - margin_m, bbox[2] + margin_m, bbox[3] + margin_m]


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


def wgs84_to_epsg3067(latitude_deg: float, longitude_deg: float) -> tuple[float, float]:
    flattening = 1.0 / GRS80_INV_F
    eccentricity_sq = flattening * (2.0 - flattening)
    second_eccentricity_sq = eccentricity_sq / (1.0 - eccentricity_sq)
    lat = math.radians(latitude_deg)
    lon_delta = math.radians(longitude_deg - EPSG3067_CENTRAL_MERIDIAN_DEG)
    n = GRS80_A / math.sqrt(1.0 - eccentricity_sq * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = second_eccentricity_sq * math.cos(lat) ** 2
    a = math.cos(lat) * lon_delta
    meridian = GRS80_A * (
        (1.0 - eccentricity_sq / 4.0 - 3.0 * eccentricity_sq**2 / 64.0 - 5.0 * eccentricity_sq**3 / 256.0) * lat
        - (3.0 * eccentricity_sq / 8.0 + 3.0 * eccentricity_sq**2 / 32.0 + 45.0 * eccentricity_sq**3 / 1024.0)
        * math.sin(2.0 * lat)
        + (15.0 * eccentricity_sq**2 / 256.0 + 45.0 * eccentricity_sq**3 / 1024.0) * math.sin(4.0 * lat)
        - (35.0 * eccentricity_sq**3 / 3072.0) * math.sin(6.0 * lat)
    )
    x = EPSG3067_FALSE_EASTING + EPSG3067_SCALE * n * (
        a
        + (1.0 - t + c) * a**3 / 6.0
        + (5.0 - 18.0 * t + t * t + 72.0 * c - 58.0 * second_eccentricity_sq) * a**5 / 120.0
    )
    y = EPSG3067_FALSE_NORTHING + EPSG3067_SCALE * (
        meridian
        + n
        * math.tan(lat)
        * (
            a * a / 2.0
            + (5.0 - t + 9.0 * c + 4.0 * c * c) * a**4 / 24.0
            + (61.0 - 58.0 * t + t * t + 600.0 * c - 330.0 * second_eccentricity_sq) * a**6 / 720.0
        )
    )
    return x, y


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


def https_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def http_json(url: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={**auth_headers(api_key), "Accept": "application/json"})
    with urllib.request.urlopen(request, context=https_context()) as response:
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
    with urllib.request.urlopen(request, context=https_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, api_key: str, output_path: Path) -> None:
    request = urllib.request.Request(url, headers=auth_headers(api_key))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    progress(f"Downloading MML result to {output_path}...")
    temporary_path = output_path.with_name(output_path.name + ".part")
    total = 0
    try:
        with urllib.request.urlopen(request, context=https_context()) as response, temporary_path.open("wb") as file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                total += len(chunk)
                if total // (10 * 1024 * 1024) != (total - len(chunk)) // (10 * 1024 * 1024):
                    progress(f"Downloaded {total / (1024 * 1024):.1f} MiB...")
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    progress(f"Downloaded {total / (1024 * 1024):.1f} MiB.")


def submit_mml_bbox_job(
    *,
    api_key: str,
    bbox: list[float],
    theme: str,
    base_url: str,
) -> str:
    return submit_bbox_process_job(
        api_key=api_key,
        bbox=bbox,
        process_id="maastotietokanta_bbox",
        file_format="GPKG",
        base_url=base_url,
        extra_inputs={"themeInput": theme},
    )


def submit_bbox_process_job(
    *,
    api_key: str,
    bbox: list[float],
    process_id: str,
    file_format: str,
    base_url: str,
    extra_inputs: dict[str, Any] | None = None,
) -> str:
    inputs: dict[str, Any] = {
        "boundingBoxInput": bbox,
        "fileFormatInput": file_format,
    }
    if extra_inputs:
        inputs.update(extra_inputs)
    payload = {
        "id": process_id,
        "inputs": inputs,
    }
    response = post_json(f"{base_url}/processes/{process_id}/execution", api_key, payload)
    for link in response.get("links", []):
        if link.get("rel") == "self" and link.get("href"):
            return str(link["href"])
    job_id = response.get("jobID")
    if not job_id:
        raise RuntimeError(f"MML execution response did not include a job link: {response}")
    return f"{base_url}/jobs/{job_id}"


def submit_map_sheet_process_job(
    *,
    api_key: str,
    map_sheets: list[str],
    process_id: str,
    file_format: str,
    base_url: str,
    extra_inputs: dict[str, Any] | None = None,
) -> str:
    inputs: dict[str, Any] = {
        "mapSheetInput": map_sheets,
        "fileFormatInput": file_format,
    }
    if extra_inputs:
        inputs.update(extra_inputs)
    payload = {
        "id": process_id,
        "inputs": inputs,
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
    last_state = ""
    while True:
        status = http_json(job_url, api_key)
        state = str(status.get("status", "")).lower()
        elapsed = time.monotonic() - started
        if state != last_state:
            progress(f"MML job status: {state or 'unknown'} ({elapsed:.0f}s elapsed)")
            last_state = state
        if state == "successful":
            return status
        if state in {"failed", "dismissed"}:
            raise RuntimeError(f"MML job {state}: {status.get('message', status)}")
        if elapsed > timeout_seconds:
            raise TimeoutError(f"MML job did not finish within {timeout_seconds:.0f} seconds: {job_url}")
        time.sleep(poll_seconds)


def mml_job_results_with_retries(
    *,
    submit_job: Any,
    api_key: str,
    poll_seconds: float,
    timeout_seconds: float,
    description: str,
    attempts: int = DEFAULT_MML_JOB_ATTEMPTS,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            progress(f"Retrying {description} MML job ({attempt}/{attempts})...")
        try:
            job_url = submit_job()
            progress(f"MML job URL: {job_url}")
            status = wait_for_job(
                job_url,
                api_key,
                poll_seconds=poll_seconds,
                timeout_seconds=timeout_seconds,
            )
            progress(f"Fetching {description} result metadata...")
            return http_json(results_url_from_status(status, job_url), api_key)
        except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            progress(f"{description} MML job attempt {attempt} failed: {exc}")
            time.sleep(min(10.0 * attempt, 30.0))
    assert last_error is not None
    raise last_error


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


def pick_download_url_by_suffix(results: dict[str, Any], suffixes: tuple[str, ...]) -> str:
    lowered_suffixes = tuple(suffix.lower() for suffix in suffixes)
    for result in results.get("results", []):
        path = str(result.get("path", ""))
        if path.lower().endswith(lowered_suffixes):
            return path
    for result in results.get("results", []):
        if result.get("zipPath"):
            return str(result["zipPath"])
    raise RuntimeError(f"MML results did not include a downloadable file ending in {suffixes}: {results}")


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


def extract_laser_paths(download_path: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(download_path):
        with zipfile.ZipFile(download_path) as archive:
            laz_names = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".las", ".laz"))
            ]
            if not laz_names:
                raise RuntimeError(f"No .las/.laz file found in {download_path}")
            for name in laz_names:
                archive.extract(name, output_dir)
            return [output_dir / name for name in laz_names]

    with download_path.open("rb") as file:
        magic = file.read(4)
    if magic == b"LASF":
        target = download_path.with_suffix(".laz")
        if download_path != target:
            if target.exists():
                target.unlink()
            download_path.replace(target)
        return [target]
    raise RuntimeError(
        f"MML laser download is neither a ZIP nor LAS/LAZ data: {download_path} "
        f"(first bytes: {magic!r})"
    )


def load_table_rules(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return dict(DEFAULT_TABLE_RULES)
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError("Mapping file must be a JSON object")
    merged = dict(DEFAULT_TABLE_RULES)
    merged.update(raw)
    return merged


def table_rules_with_optional_forest_mask(
    table_rules: dict[str, dict[str, Any]],
    *,
    include_forest_mask: bool,
) -> dict[str, dict[str, Any]]:
    rules = dict(table_rules)
    if include_forest_mask and "metsamaankasvillisuus" not in rules:
        rules["metsamaankasvillisuus"] = {
            "object_type": "area",
            "symbol": "406",
        }
    return rules


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
            progress(f"Converting table {table}...")
            rows_seen = 0
            rows_kept = 0
            for row in feature_rows(connection, table, geometry_column):
                rows_seen += 1
                geometry = parse_gpkg_geometry(row[geometry_column])
                if geometry is None or (bbox is not None and not geometry_intersects_bbox(geometry, bbox)):
                    continue
                if clip_frame is not None:
                    geometry = clip_geometry_to_frame(geometry, clip_frame)
                    if geometry is None:
                        continue
                properties = row_properties(row, geometry_column)
                object_type, symbol = classify_feature(table, properties, geometry, rule)
                metadata = iof_symbol_metadata(symbol)
                iof_symbol_number = metadata.get("iof_symbol_number")
                if (symbol is None or iof_symbol_number is None) and not include_unmapped:
                    continue
                properties.update(
                    {
                        "source": "Maanmittauslaitos Maastotietokanta",
                        "source_table": table,
                        "object_type": object_type or object_type_from_geojson(geometry["type"]),
                        **metadata,
                    }
                )
                if iof_symbol_number is not None:
                    properties["symbol"] = iof_symbol_number
                features.append({"type": "Feature", "properties": properties, "geometry": geometry})
                rows_kept += 1
                if rows_seen % 10000 == 0:
                    progress(f"  {table}: scanned {rows_seen} rows, kept {rows_kept} features...")
            progress(f"  {table}: scanned {rows_seen} rows, kept {rows_kept} features.")
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


def iof_symbol_metadata(symbol: str | None) -> dict[str, Any]:
    return symbol_library_metadata(symbol)


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


def read_xyz_grid(path: Path) -> tuple[list[float], list[float], dict[tuple[float, float], float]]:
    points: dict[tuple[float, float], float] = {}
    xs: set[float] = set()
    ys: set[float] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part for part in line.replace(",", " ").split() if part]
        if len(parts) < 3:
            raise ValueError(f"Invalid XYZ row at {path}:{line_number}")
        try:
            x, y, z = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError as exc:
            if line_number == 1:
                continue
            raise ValueError(f"Invalid numeric XYZ row at {path}:{line_number}") from exc
        xs.add(x)
        ys.add(y)
        points[(x, y)] = z
    if len(xs) < 2 or len(ys) < 2:
        raise ValueError("XYZ input must contain a regular grid with at least two x and two y values")
    return sorted(xs), sorted(ys), points


def interpolate_contour_point(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    level: float,
) -> list[float]:
    ax, ay, az = a
    bx, by, bz = b
    if abs(bz - az) < 1e-12:
        t = 0.5
    else:
        t = (level - az) / (bz - az)
    t = min(max(t, 0.0), 1.0)
    return [ax + (bx - ax) * t, ay + (by - ay) * t]


def contour_cell_segments(corners: list[tuple[float, float, float]], level: float) -> list[tuple[list[float], list[float]]]:
    edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    intersections = []
    for start, end in edges:
        a = corners[start]
        b = corners[end]
        da = a[2] - level
        db = b[2] - level
        if da == 0.0 and db == 0.0:
            continue
        if da == 0.0:
            intersections.append([a[0], a[1]])
        elif db == 0.0:
            intersections.append([b[0], b[1]])
        elif (da < 0.0 < db) or (db < 0.0 < da):
            intersections.append(interpolate_contour_point(a, b, level))
    unique = []
    seen = set()
    for point in intersections:
        key = (round(point[0], 6), round(point[1], 6))
        if key in seen:
            continue
        seen.add(key)
        unique.append(point)
    if len(unique) == 2:
        return [(unique[0], unique[1])]
    if len(unique) == 4:
        return [(unique[0], unique[1]), (unique[2], unique[3])]
    return []


def stitch_segments(segments: list[tuple[list[float], list[float]]]) -> list[list[list[float]]]:
    lines: list[list[list[float]]] = []
    endpoint_to_line: dict[tuple[int, int], int] = {}

    def key(point: list[float]) -> tuple[int, int]:
        return (round(point[0] * 1000), round(point[1] * 1000))

    for start, end in segments:
        start_key = key(start)
        end_key = key(end)
        start_line_index = endpoint_to_line.get(start_key)
        end_line_index = endpoint_to_line.get(end_key)
        if start_line_index is None and end_line_index is None:
            endpoint_to_line[start_key] = len(lines)
            endpoint_to_line[end_key] = len(lines)
            lines.append([start, end])
            continue
        if start_line_index is not None and end_line_index is None:
            line = lines[start_line_index]
            endpoint_to_line.pop(key(line[0]), None)
            endpoint_to_line.pop(key(line[-1]), None)
            if key(line[0]) == start_key:
                line.insert(0, end)
            else:
                line.append(end)
            endpoint_to_line[key(line[0])] = start_line_index
            endpoint_to_line[key(line[-1])] = start_line_index
            continue
        if end_line_index is not None and start_line_index is None:
            line = lines[end_line_index]
            endpoint_to_line.pop(key(line[0]), None)
            endpoint_to_line.pop(key(line[-1]), None)
            if key(line[0]) == end_key:
                line.insert(0, start)
            else:
                line.append(start)
            endpoint_to_line[key(line[0])] = end_line_index
            endpoint_to_line[key(line[-1])] = end_line_index
            continue
    return [line for line in lines if len(line) >= 2]


def contour_features_from_xyz_grid(
    xs: list[float],
    ys: list[float],
    points: dict[tuple[float, float], float],
    *,
    interval_m: float,
    min_level: float | None = None,
    max_level: float | None = None,
    simplify_tolerance_m: float = 0.0,
    index_contour_every: int = 5,
) -> list[dict[str, Any]]:
    import contourpy
    import numpy as np

    if interval_m <= 0:
        raise ValueError("--interval-m must be greater than zero")
    elevations = list(points.values())
    min_elevation = min_level if min_level is not None else min(elevations)
    max_elevation = max_level if max_level is not None else max(elevations)
    start = math.ceil(min_elevation / interval_m) * interval_m
    end = math.floor(max_elevation / interval_m) * interval_m
    if start <= min_elevation:
        start += interval_m
    if end >= max_elevation:
        end -= interval_m
    z = np.empty((len(ys), len(xs)), dtype=float)
    for y_index, y in enumerate(ys):
        for x_index, x in enumerate(xs):
            try:
                z[y_index, x_index] = points[(x, y)]
            except KeyError as exc:
                raise ValueError("XYZ input must be a complete regular grid for contour generation") from exc
    generator = contourpy.contour_generator(
        x=np.asarray(xs, dtype=float),
        y=np.asarray(ys, dtype=float),
        z=z,
        name="serial",
        line_type=contourpy.LineType.Separate,
    )
    features: list[dict[str, Any]] = []
    level = start
    level_index = 1
    while level <= end + interval_m * 0.001:
        render_symbol, iof_number, iof_name = contour_symbol_for_level_index(level_index, index_contour_every)
        for contour_line in generator.lines(level):
            line = [[float(point[0]), float(point[1])] for point in contour_line]
            line = simplify_line(line, simplify_tolerance_m)
            if len(line) < 2:
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "LiDAR-derived elevation grid",
                        "source_table": "lidar_xyz_grid",
                        "object_type": "line",
                        "symbol": iof_number,
                        "iof_symbol_number": iof_number,
                        "iof_symbol_name": iof_name,
                        "render_symbol": render_symbol,
                        "korkeusarvo": int(round(level * 1000)),
                    },
                    "geometry": {"type": "LineString", "coordinates": line},
                }
            )
        level += interval_m
        level_index += 1
    return features


def contour_symbol_for_level_index(level_index: int, index_contour_every: int) -> tuple[str, str, str]:
    if index_contour_every > 0 and level_index % index_contour_every == 0:
        return "index_contour", "102", "Index contour"
    return "contour", "101", "Contour"


def simplify_line(line: list[list[float]], tolerance: float) -> list[list[float]]:
    if tolerance <= 0 or len(line) <= 2:
        return line
    return douglas_peucker(line, tolerance)


def douglas_peucker(points: list[list[float]], tolerance: float) -> list[list[float]]:
    if len(points) <= 2:
        return points
    start = points[0]
    end = points[-1]
    max_distance = -1.0
    max_index = 0
    for index, point in enumerate(points[1:-1], start=1):
        distance = point_line_distance(point, start, end)
        if distance > max_distance:
            max_distance = distance
            max_index = index
    if max_distance <= tolerance:
        return [start, end]
    left = douglas_peucker(points[: max_index + 1], tolerance)
    right = douglas_peucker(points[max_index:], tolerance)
    return left[:-1] + right


def point_line_distance(point: list[float], start: list[float], end: list[float]) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return math.hypot(px - sx, py - sy)
    return abs(dy * px - dx * py + ex * sy - ey * sx) / math.hypot(dx, dy)


def grid_spacing(values: list[float]) -> float:
    differences = [values[index] - values[index - 1] for index in range(1, len(values))]
    positive = [value for value in differences if value > 0]
    if not positive:
        raise ValueError("Grid values must contain at least two distinct coordinates")
    return min(positive)


def grid_corner_segments(
    xs: list[float],
    ys: list[float],
    values: dict[tuple[float, float], float],
    *,
    level: float,
) -> list[tuple[list[float], list[float]]]:
    segments: list[tuple[list[float], list[float]]] = []
    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            required = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            if any(point not in values for point in required):
                continue
            corners = [(x, y, values[(x, y)]) for x, y in required]
            cell_values = [corner[2] for corner in corners]
            if level < min(cell_values) or level > max(cell_values):
                continue
            segments.extend(contour_cell_segments(corners, level))
    return segments


def slope_degrees_from_xyz_grid(
    xs: list[float],
    ys: list[float],
    points: dict[tuple[float, float], float],
) -> dict[tuple[float, float], float]:
    dx = grid_spacing(xs)
    dy = grid_spacing(ys)
    slopes: dict[tuple[float, float], float] = {}
    for x_index, x in enumerate(xs):
        for y_index, y in enumerate(ys):
            left = xs[max(x_index - 1, 0)]
            right = xs[min(x_index + 1, len(xs) - 1)]
            down = ys[max(y_index - 1, 0)]
            up = ys[min(y_index + 1, len(ys) - 1)]
            required = [(left, y), (right, y), (x, down), (x, up)]
            if any(point not in points for point in required):
                continue
            local_dx = right - left if right != left else dx
            local_dy = up - down if up != down else dy
            dz_dx = (points[(right, y)] - points[(left, y)]) / local_dx
            dz_dy = (points[(x, up)] - points[(x, down)]) / local_dy
            slopes[(x, y)] = math.degrees(math.atan(math.hypot(dz_dx, dz_dy)))
    return slopes


def cliff_features_from_xyz_grid(
    xs: list[float],
    ys: list[float],
    points: dict[tuple[float, float], float],
    *,
    slope_threshold_deg: float,
    min_length_m: float,
) -> list[dict[str, Any]]:
    slopes = slope_degrees_from_xyz_grid(xs, ys, points)
    lines = stitch_segments(grid_corner_segments(xs, ys, slopes, level=slope_threshold_deg))
    features = []
    for line in lines:
        length = line_length_m(line)
        if length < min_length_m:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "source": "LiDAR-derived elevation grid",
                    "source_table": "lidar_xyz_grid",
                    "object_type": "line",
                    "symbol": "202",
                    "iof_symbol_number": "202",
                    "iof_symbol_name": "Cliff",
                    "slope_threshold_deg": slope_threshold_deg,
                },
                "geometry": {"type": "LineString", "coordinates": line},
            }
        )
    return features


def line_length_m(line: list[list[float]]) -> float:
    length = 0.0
    for a, b in zip(line, line[1:]):
        length += math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
    return length


def read_lidar_point_rows(path: Path) -> list[tuple[float, float, float, int | None]]:
    if path.suffix.lower() in {".las", ".laz"}:
        try:
            import laspy
        except ImportError as exc:
            raise RuntimeError("LAS/LAZ input requires the laspy[lazrs] dependency") from exc
        las = laspy.read(path)
        classifications = getattr(las, "classification", [None] * len(las.x))
        return [
            (float(x), float(y), float(z), int(classification) if classification is not None else None)
            for x, y, z, classification in zip(las.x, las.y, las.z, classifications)
        ]
    rows: list[tuple[float, float, float, int | None]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part for part in line.replace(",", " ").split() if part]
        if len(parts) < 3:
            raise ValueError(f"Invalid point row at {path}:{line_number}")
        try:
            classification = int(float(parts[3])) if len(parts) > 3 else None
            rows.append((float(parts[0]), float(parts[1]), float(parts[2]), classification))
        except ValueError as exc:
            if line_number == 1:
                continue
            raise ValueError(f"Invalid numeric point row at {path}:{line_number}") from exc
    if not rows:
        raise ValueError("Point input did not contain any points")
    return rows


def read_lidar_point_rows_many(paths: list[Path]) -> list[tuple[float, float, float, int | None]]:
    rows: list[tuple[float, float, float, int | None]] = []
    for path in paths:
        rows.extend(read_lidar_point_rows(path))
    return rows


def tm35_map_sheets_for_bbox(bbox: list[float]) -> list[str]:
    try:
        from tm35fin import Coordinates
    except ImportError as exc:
        raise RuntimeError("Automatic MML laser sheet resolution requires the tm35fin dependency") from exc

    min_x, min_y, max_x, max_y = bbox
    sample_step_m = MML_LASER_MAP_SHEET_GRID_M
    xs = coordinate_samples(min_x, max_x, sample_step_m)
    ys = coordinate_samples(min_y, max_y, sample_step_m)
    names = {
        Coordinates(x, y).tile.name
        for x in xs
        for y in ys
    }
    return sorted(names)


def coordinate_samples(min_value: float, max_value: float, step: float) -> list[float]:
    values = [min_value]
    current = math.floor(min_value / step) * step
    while current <= max_value:
        if min_value <= current <= max_value:
            values.append(current)
        current += step
    values.append(max_value)
    return sorted(set(values))


def ground_grid_from_lidar_points(
    rows: list[tuple[float, float, float, int | None]],
    *,
    bbox: list[float],
    cell_size_m: float,
    ground_quantile: float = 0.5,
    smoothing_sigma_m: float = 1.5,
    fill_max_distance_m: float = 12.0,
) -> tuple[list[float], list[float], dict[tuple[float, float], float], dict[str, Any]]:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Point-cloud ground gridding requires numpy") from exc

    if cell_size_m <= 0:
        raise ValueError("--ground-cell-size-m must be greater than zero")
    if smoothing_sigma_m < 0:
        raise ValueError("--ground-smoothing-sigma-m must be zero or greater")
    if not 0.0 <= ground_quantile <= 1.0:
        raise ValueError("--ground-quantile must be between 0 and 1")
    min_x, min_y, max_x, max_y = bbox
    ground_rows = [
        (x, y, z)
        for x, y, z, classification in rows
        if min_x <= x <= max_x
        and min_y <= y <= max_y
        and (classification == 2 or classification is None)
    ]
    if not ground_rows:
        raise ValueError("No classified ground points found inside the map frame")

    columns = int(math.ceil((max_x - min_x) / cell_size_m)) + 1
    rows_count = int(math.ceil((max_y - min_y) / cell_size_m)) + 1
    xs = [min_x + column * cell_size_m for column in range(columns)]
    ys = [min_y + row * cell_size_m for row in range(rows_count)]
    grid = np.full((rows_count, columns), np.nan, dtype=float)
    confidence = np.zeros((rows_count, columns), dtype=float)
    cell_values: dict[tuple[int, int], list[float]] = {}
    for x, y, z in ground_rows:
        column = min(max(int(round((x - min_x) / cell_size_m)), 0), columns - 1)
        row = min(max(int(round((y - min_y) / cell_size_m)), 0), rows_count - 1)
        cell_values.setdefault((row, column), []).append(z)

    residuals: list[float] = []
    cell_noise_values: list[float] = []
    for (row, column), values in cell_values.items():
        sample = np.asarray(values, dtype=float)
        estimate = float(np.quantile(sample, ground_quantile))
        grid[row, column] = estimate
        residuals.extend(abs(float(value) - estimate) for value in sample)

    global_noise_m = robust_noise_m(np.asarray(residuals, dtype=float), default=0.15)
    for (row, column), values in cell_values.items():
        sample = np.asarray(values, dtype=float)
        cell_noise_m = robust_noise_m(abs(sample - grid[row, column]), default=global_noise_m)
        cell_noise_values.append(cell_noise_m)
        # Confidence is inverse variance of the cell mean. Cap tiny noise values
        # so dense flat cells do not dominate the whole smoothed surface.
        confidence[row, column] = len(values) / max(cell_noise_m, 0.05) ** 2

    known_row, known_column = np.where(~np.isnan(grid))
    known_xy = np.column_stack((known_column.astype(float), known_row.astype(float))) * cell_size_m
    known_z = grid[known_row, known_column]
    known_confidence = confidence[known_row, known_column]
    all_row, all_column = np.indices(grid.shape)
    missing = np.isnan(grid)
    if np.any(missing):
        query_xy = np.column_stack((all_column[missing].astype(float), all_row[missing].astype(float))) * cell_size_m
        distances, indexes = nearest_ground_cells(
            known_xy,
            query_xy,
            neighbor_count=min(8, len(known_z)),
            max_distance_m=fill_max_distance_m,
        )
        filled = []
        for point_distances, point_indexes in zip(distances, indexes):
            valid = point_indexes < len(known_z)
            if not np.any(valid):
                filled.append(np.nan)
                continue
            valid_distances = point_distances[valid]
            valid_indexes = point_indexes[valid]
            zero = valid_distances <= 1e-9
            if np.any(zero):
                filled.append(float(known_z[valid_indexes[zero][0]]))
                continue
            weights = 1.0 / (valid_distances * valid_distances)
            filled.append(float(np.sum(weights * known_z[valid_indexes]) / np.sum(weights)))
        grid[missing] = np.asarray(filled)
        confidence[missing] = max(float(np.nanmedian(known_confidence)) * 0.10, 1e-6)
    if np.isnan(grid).any():
        raise ValueError("Point cloud has ground gaps too large to build a complete contour grid")

    if smoothing_sigma_m > 0:
        try:
            from scipy.ndimage import gaussian_filter
        except ImportError as exc:
            raise RuntimeError("Ground model smoothing requires scipy") from exc
        sigma_cells = smoothing_sigma_m / cell_size_m
        weighted = gaussian_filter(grid * confidence, sigma=sigma_cells, mode="nearest")
        weights = gaussian_filter(confidence, sigma=sigma_cells, mode="nearest")
        grid = weighted / np.maximum(weights, 1e-12)

    points = {
        (xs[column], ys[row]): float(grid[row, column])
        for row in range(rows_count)
        for column in range(columns)
    }
    report = {
        "model": "weighted Gaussian-smoothed regular grid from classified ground points",
        "observation_model": "elevation_observation(x,y) = mu(x,y) + epsilon",
        "ground_point_count": len(ground_rows),
        "grid_columns": columns,
        "grid_rows": rows_count,
        "grid_cell_size_m": cell_size_m,
        "observed_cell_count": len(cell_values),
        "interpolated_cell_count": int(np.count_nonzero(missing)),
        "ground_quantile": ground_quantile,
        "ground_smoothing_sigma_m": smoothing_sigma_m,
        "fill_max_distance_m": fill_max_distance_m,
        "global_noise_estimate_m": global_noise_m,
        "median_cell_noise_estimate_m": float(np.median(cell_noise_values)) if cell_noise_values else global_noise_m,
        "p90_cell_noise_estimate_m": float(np.quantile(cell_noise_values, 0.9)) if cell_noise_values else global_noise_m,
    }
    return xs, ys, points, report


def nearest_ground_cells(
    known_xy: Any,
    query_xy: Any,
    *,
    neighbor_count: int,
    max_distance_m: float,
) -> tuple[Any, Any]:
    import numpy as np

    try:
        from scipy.spatial import cKDTree
    except ImportError:
        distances_out = []
        indexes_out = []
        for query in query_xy:
            distances = np.hypot(known_xy[:, 0] - query[0], known_xy[:, 1] - query[1])
            order = np.argsort(distances)[:neighbor_count]
            point_distances = distances[order]
            point_indexes = order.astype(int)
            valid = point_distances <= max_distance_m
            padded_distances = np.full(neighbor_count, np.inf, dtype=float)
            padded_indexes = np.full(neighbor_count, len(known_xy), dtype=int)
            padded_distances[: np.count_nonzero(valid)] = point_distances[valid]
            padded_indexes[: np.count_nonzero(valid)] = point_indexes[valid]
            distances_out.append(padded_distances)
            indexes_out.append(padded_indexes)
        return np.asarray(distances_out), np.asarray(indexes_out)

    tree = cKDTree(known_xy)
    distances, indexes = tree.query(query_xy, k=neighbor_count, distance_upper_bound=max_distance_m)
    if neighbor_count == 1:
        distances = distances[:, np.newaxis]
        indexes = indexes[:, np.newaxis]
    return distances, indexes


def robust_noise_m(residuals: Any, *, default: float) -> float:
    import numpy as np

    values = np.asarray(residuals, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return default
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    estimate = 1.4826 * mad
    if estimate <= 1e-9:
        return max(median, default)
    return estimate


def lidar_vegetation_features(
    rows: list[tuple[float, float, float, int | None]],
    *,
    cell_size_m: float,
    min_height_m: float,
    slow_count: int,
    fight_count: int,
) -> list[dict[str, Any]]:
    if cell_size_m <= 0:
        raise ValueError("--cell-size-m must be greater than zero")
    min_x = min(row[0] for row in rows)
    min_y = min(row[1] for row in rows)
    ground: dict[tuple[int, int], float] = {}
    vegetation_counts: dict[tuple[int, int], int] = {}
    for x, y, z, classification in rows:
        cell = (math.floor((x - min_x) / cell_size_m), math.floor((y - min_y) / cell_size_m))
        if classification == 2 or cell not in ground or z < ground[cell]:
            ground[cell] = z
    for x, y, z, classification in rows:
        if classification == 2:
            continue
        cell = (math.floor((x - min_x) / cell_size_m), math.floor((y - min_y) / cell_size_m))
        ground_z = ground.get(cell)
        if ground_z is None:
            continue
        if z - ground_z >= min_height_m:
            vegetation_counts[cell] = vegetation_counts.get(cell, 0) + 1
    cells_by_symbol: dict[str, list[tuple[float, float, float, float, int]]] = {"406": [], "410": []}
    for (cell_x, cell_y), count in vegetation_counts.items():
        if count < slow_count:
            continue
        symbol = "410" if count >= fight_count else "406"
        x0 = min_x + cell_x * cell_size_m
        y0 = min_y + cell_y * cell_size_m
        x1 = x0 + cell_size_m
        y1 = y0 + cell_size_m
        cells_by_symbol[symbol].append((x0, y0, x1, y1, count))
    features: list[dict[str, Any]] = []
    for symbol, cells in cells_by_symbol.items():
        if not cells:
            continue
        name = "Vegetation: fight" if symbol == "410" else "Vegetation: slow running"
        geometries = dissolve_rectangular_cells(cells)
        for geometry in geometries:
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "LiDAR point cloud",
                        "source_table": "lidar_points",
                        "object_type": "area",
                        "symbol": symbol,
                        "iof_symbol_number": symbol,
                        "iof_symbol_name": name,
                        "cell_size_m": cell_size_m,
                    },
                    "geometry": geometry,
                }
            )
    return features


def dissolve_rectangular_cells(cells: list[tuple[float, float, float, float, int]]) -> list[dict[str, Any]]:
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError:
        return [
            {
                "type": "Polygon",
                "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
            }
            for x0, y0, x1, y1, _count in cells
        ]
    polygons = [
        Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        for x0, y0, x1, y1, _count in cells
    ]
    dissolved = unary_union(polygons)
    shapely_geometries = list(dissolved.geoms) if hasattr(dissolved, "geoms") else [dissolved]
    output = []
    for geometry in shapely_geometries:
        if geometry.is_empty:
            continue
        if geometry.geom_type == "Polygon":
            output.append(shapely_polygon_to_geojson(geometry))
    return output


def shapely_polygon_to_geojson(polygon: Any) -> dict[str, Any]:
    exterior = [[float(x), float(y)] for x, y in polygon.exterior.coords]
    holes = [
        [[float(x), float(y)] for x, y in interior.coords]
        for interior in polygon.interiors
    ]
    return {
        "type": "Polygon",
        "coordinates": [exterior, *holes],
    }


def combined_terrain_geojson(
    *,
    contour_features: list[dict[str, Any]],
    cliff_features: list[dict[str, Any]],
    vegetation_features: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "mml-omap-lidar-terrain",
        "crs": {"type": "name", "properties": {"name": "EPSG:3067"}},
        "features": [*vegetation_features, *contour_features, *cliff_features],
    }


def add_map_frame_if_requested(
    geojson: dict[str, Any],
    *,
    bbox_raw: str | None,
    magnetic_declination_deg_raw: str,
    magnetic_date_raw: str | None,
) -> dict[str, Any]:
    if not bbox_raw:
        return geojson
    bbox = parse_orienteering_bbox(bbox_raw)
    magnetic_date = parse_date(magnetic_date_raw)
    magnetic_declination_deg = resolve_magnetic_declination_deg(
        magnetic_declination_deg_raw,
        bbox=bbox,
        magnetic_date=magnetic_date.isoformat(),
    )
    output = clip_geojson_to_frame(geojson, OrientedFrame(bbox, magnetic_declination_deg))
    output["map_frame"] = {
        "bbox": bbox,
        "magnetic_declination_deg": magnetic_declination_deg,
        "magnetic_date": magnetic_date.isoformat(),
    }
    return output


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
    symbol = str(properties.get("symbol", properties.get("source_table", "unknown")))
    iof_symbol_number = properties.get("iof_symbol_number")
    if iof_symbol_number is not None:
        mapped = IOF_NUMBER_TO_RENDER_SYMBOL.get(str(iof_symbol_number))
        if mapped:
            return mapped
    mapped = IOF_NUMBER_TO_RENDER_SYMBOL.get(symbol)
    if mapped:
        return mapped
    if properties.get("source_table") == "tieviiva":
        kohdeluokka = str(properties.get("kohdeluokka", ""))
        if kohdeluokka in {"12111", "12112", "12121", "12122", "12131", "12132"}:
            return "major_road"
        if kohdeluokka in {"12141", "12142", "12314"}:
            return "road"
        if kohdeluokka in {"12316"}:
            return "small_road"
        if kohdeluokka in {"12313"}:
            return "path"
        if kohdeluokka in {"12311", "12312", "12315", "12317"}:
            return "small_path"
    if properties.get("source_table") == "virtavesikapea" and str(properties.get("kohdeluokka", "")) == "36312":
        return "wide_stream"
    if properties.get("source_table") == "maatalousmaa" and symbol == "field":
        return "cultivated_land"
    if properties.get("source_table") == "urheilujavirkistysalue" and symbol == "field":
        return "recreation_area"
    return symbol


def feature_style(feature: dict[str, Any]) -> dict[str, Any]:
    return SYMBOL_STYLES.get(feature_symbol(feature), DEFAULT_STYLE)


def line_style_layers(style: dict[str, Any]) -> list[dict[str, Any]]:
    inner_stroke = style.get("inner_stroke")
    if not inner_stroke:
        return [style]
    return [
        {
            "stroke": style.get("stroke", "none"),
            "stroke_width_mm": style.get("stroke_width_mm", 0.18),
            "dasharray": style.get("dasharray"),
        },
        {
            "stroke": inner_stroke,
            "stroke_width_mm": style.get("inner_stroke_width_mm", style.get("stroke_width_mm", 0.18)),
            "dasharray": style.get("inner_dasharray"),
        },
    ]


def point_radius_mm(style: dict[str, Any]) -> float:
    return float(style.get("point_radius_mm", max(float(style.get("stroke_width_mm", 0.18)) * 2.0, 0.35)))


def feature_label_text(feature: dict[str, Any]) -> str | None:
    symbol = feature_symbol(feature)
    if symbol not in {"place_label", "water_label"}:
        return None
    text = (feature.get("properties") or {}).get("teksti")
    return str(text) if text else None


def should_render_point_symbol(feature: dict[str, Any]) -> bool:
    properties = feature.get("properties") or {}
    object_type = properties.get("object_type")
    if object_type is not None and object_type != "point":
        return False
    return feature_symbol(feature) in {"mapped_rock"} or object_type == "point"


def feature_render_order(feature: dict[str, Any]) -> int:
    symbol = feature_symbol(feature)
    if symbol in SYMBOL_RENDER_ORDER:
        return SYMBOL_RENDER_ORDER[symbol]
    properties = feature.get("properties") or {}
    object_type = properties.get("object_type")
    if object_type == "area":
        return 200
    if object_type == "line":
        return 450
    if object_type == "point":
        return 650
    return 700


def sorted_render_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        feature
        for _, feature in sorted(
            enumerate(features),
            key=lambda item: (feature_render_order(item[1]), item[0]),
        )
    ]


def line_parts_from_geometry(geometry: dict[str, Any]) -> list[list[Any]]:
    geometry_type = geometry.get("type")
    if geometry_type == "LineString":
        return [geometry.get("coordinates") or []]
    if geometry_type == "MultiLineString":
        return list(geometry.get("coordinates") or [])
    return []


def line_endpoint_distance(a: Any, b: Any) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def append_connected_line(base: list[Any], candidate: list[Any], tolerance_m: float) -> list[Any] | None:
    if not base or not candidate:
        return None
    options = [
        (line_endpoint_distance(base[-1], candidate[0]), base + candidate[1:]),
        (line_endpoint_distance(base[-1], candidate[-1]), base + list(reversed(candidate[:-1]))),
        (line_endpoint_distance(base[0], candidate[-1]), candidate[:-1] + base),
        (line_endpoint_distance(base[0], candidate[0]), list(reversed(candidate[1:])) + base),
    ]
    distance, merged = min(options, key=lambda item: item[0])
    return merged if distance <= tolerance_m else None


def merge_lines_by_endpoint(lines: list[list[Any]], tolerance_m: float) -> list[list[Any]]:
    merged = [list(line) for line in lines if len(line) > 1]
    changed = True
    while changed:
        changed = False
        for index in range(len(merged)):
            if changed:
                break
            for other_index in range(index + 1, len(merged)):
                connected = append_connected_line(merged[index], merged[other_index], tolerance_m)
                if connected is None:
                    continue
                merged[index] = connected
                del merged[other_index]
                changed = True
                break
    return merged


def merge_contour_features(features: list[dict[str, Any]], tolerance_m: float = DEFAULT_CONTOUR_MERGE_TOLERANCE_M) -> list[dict[str, Any]]:
    if tolerance_m <= 0:
        return features
    output: list[dict[str, Any]] = []
    contour_groups: dict[Any, dict[str, Any]] = {}
    for feature in features:
        if feature_symbol(feature) != "contour":
            output.append(feature)
            continue
        properties = feature.get("properties") or {}
        key = properties.get("korkeusarvo")
        group = contour_groups.setdefault(key, {"template": feature, "lines": []})
        group["lines"].extend(line_parts_from_geometry(feature.get("geometry") or {}))
    for group in contour_groups.values():
        lines = merge_lines_by_endpoint(group["lines"], tolerance_m)
        if not lines:
            continue
        template = group["template"]
        properties = dict(template.get("properties") or {})
        properties["merged_parts"] = len(group["lines"])
        geometry = {"type": "LineString", "coordinates": lines[0]}
        if len(lines) > 1:
            geometry = {"type": "MultiLineString", "coordinates": lines}
        output.append({"type": "Feature", "properties": properties, "geometry": geometry})
    return output


def dedupe_label_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, int]] = set()
    for feature in features:
        label = feature_label_text(feature)
        if not label:
            output.append(feature)
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            output.append(feature)
            continue
        coordinates = geometry.get("coordinates") or [0, 0]
        key = (feature_symbol(feature), label, round(float(coordinates[0]) * 10), round(float(coordinates[1]) * 10))
        if key in seen:
            continue
        seen.add(key)
        output.append(feature)
    return output


def prepare_render_features(geojson: dict[str, Any], contour_merge_tolerance_m: float) -> list[dict[str, Any]]:
    features = merge_contour_features(geojson_features(geojson), contour_merge_tolerance_m)
    return sorted_render_features(dedupe_label_features(features))


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


def polygon_part_mm_bbox(part: dict[str, Any], transform: RenderTransform) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for ring in part.get("coordinates") or []:
        for coordinate in ring:
            x, y = transform.to_mm(coordinate)
            xs.append(x)
            ys.append(y)
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def map_title(output_path: Path, explicit_title: str | None) -> str:
    return explicit_title if explicit_title else output_path.stem


def contour_interval_text(contour_interval_m: float) -> str:
    if contour_interval_m <= 0:
        return "Contours: MML source"
    if float(contour_interval_m).is_integer():
        return f"Contours {int(contour_interval_m)} m"
    return f"Contours {contour_interval_m:g} m"


def infer_contour_interval_m(geojson: dict[str, Any]) -> float | None:
    values: list[float] = []
    for feature in geojson_features(geojson):
        if feature_symbol(feature) != "contour":
            continue
        raw_value = (feature.get("properties") or {}).get("korkeusarvo")
        if isinstance(raw_value, (int, float)):
            value = float(raw_value)
            if abs(value) > 1000:
                value /= 1000.0
            values.append(value)
    unique = sorted(set(values))
    differences = [
        round(unique[index] - unique[index - 1], 6)
        for index in range(1, len(unique))
        if unique[index] > unique[index - 1]
    ]
    return min(differences) if differences else None


def resolve_contour_interval_m(raw_value: str, geojson: dict[str, Any]) -> float:
    if raw_value.lower() == "auto":
        return infer_contour_interval_m(geojson) or 5.0
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ValueError("--contour-interval-m must be a number of meters or auto") from exc


def map_footer_text(transform: RenderTransform, map_maker: str, contour_interval_m: float) -> str:
    return (
        f"Scale 1:{transform.scale} | {map_maker} | "
        f"{contour_interval_text(contour_interval_m)} | "
        f"KOK {transform.magnetic_declination_deg:.2f} deg | EPSG:3067"
    )


def north_line_x_positions(transform: RenderTransform, spacing_m: float) -> list[float]:
    if spacing_m <= 0:
        return []
    spacing_mm = spacing_m * 1000.0 / transform.scale
    if spacing_mm <= 0:
        return []
    positions: list[float] = []
    x = transform.margin_mm + spacing_mm
    max_x = transform.margin_mm + transform.map_width_mm
    while x < max_x - 0.001:
        positions.append(x)
        x += spacing_mm
    return positions


def append_svg_layout(
    lines: list[str],
    output_path: Path,
    transform: RenderTransform,
    *,
    map_title_text: str | None,
    map_maker: str,
    contour_interval_m: float,
    north_line_spacing_m: float,
) -> None:
    for x in north_line_x_positions(transform, north_line_spacing_m):
        lines.append(
            f'<line x1="{x:.3f}" y1="{transform.margin_mm:.3f}" '
            f'x2="{x:.3f}" y2="{transform.margin_mm + transform.map_height_mm:.3f}" '
            f'stroke="#6f2dbd" stroke-width="0.180"/>'
        )
    frame_x = transform.margin_mm
    frame_y = transform.margin_mm
    lines.append(
        f'<rect x="{frame_x:.3f}" y="{frame_y:.3f}" '
        f'width="{transform.map_width_mm:.3f}" height="{transform.map_height_mm:.3f}" '
        f'fill="none" stroke="#000000" stroke-width="0.120"/>'
    )
    title = html.escape(map_title(output_path, map_title_text))
    footer = html.escape(map_footer_text(transform, map_maker, contour_interval_m))
    lines.append(
        f'<text x="{transform.margin_mm:.3f}" y="{max(transform.margin_mm - 1.2, 3.0):.3f}" '
        f'font-family="Arial, Helvetica, sans-serif" font-size="3.2" fill="#000000">{title}</text>'
    )
    lines.append(
        f'<text x="{transform.margin_mm:.3f}" y="{transform.page_height_mm - 1.5:.3f}" '
        f'font-family="Arial, Helvetica, sans-serif" font-size="2.6" fill="#000000">{footer}</text>'
    )
    north_x = transform.page_width_mm - transform.margin_mm - 4.0
    north_y = max(transform.margin_mm - 1.0, 4.5)
    lines.append(
        f'<text x="{north_x:.3f}" y="{north_y:.3f}" text-anchor="middle" '
        f'font-family="Arial, Helvetica, sans-serif" font-size="3.0" fill="#000000">N</text>'
    )


def render_svg(
    geojson: dict[str, Any],
    output_path: Path,
    *,
    transform: RenderTransform,
    include_layout: bool = True,
    map_title_text: str | None = None,
    map_maker: str = "mml-omap",
    contour_interval_m: float = 5.0,
    north_line_spacing_m: float = DEFAULT_NORTH_LINE_SPACING_M,
    contour_merge_tolerance_m: float = DEFAULT_CONTOUR_MERGE_TOLERANCE_M,
) -> None:
    features = prepare_render_features(geojson, contour_merge_tolerance_m)
    progress(f"Rendering SVG with {len(features)} features...")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{transform.page_width_mm:.3f}mm" height="{transform.page_height_mm:.3f}mm" '
            f'viewBox="0 0 {transform.page_width_mm:.3f} {transform.page_height_mm:.3f}">'
        ),
        (
            '<defs>'
            '<pattern id="cultivated-land" patternUnits="userSpaceOnUse" width="2.0" height="2.0">'
            '<rect width="2.0" height="2.0" fill="#f2c84b"/>'
            '<circle cx="1.0" cy="1.0" r="0.12" fill="#000000"/>'
            '</pattern>'
            '</defs>'
        ),
        '<rect x="0" y="0" width="100%" height="100%" fill="#ffffff"/>',
    ]
    for index, feature in enumerate(features, start=1):
        if index % 10000 == 0:
            progress(f"  rendered {index}/{len(features)} features...")
        style = feature_style(feature)
        stroke = style.get("stroke", "none")
        fill = style.get("fill", "none")
        stroke_width = float(style.get("stroke_width_mm", 0.18))
        fill_pattern = style.get("svg_fill_pattern")
        if fill_pattern == "cultivated_land":
            fill = "url(#cultivated-land)"
        for part in iter_geometry_parts(feature.get("geometry") or {}):
            if part["type"] == "Polygon":
                path = " ".join(svg_path_for_ring(ring, transform) for ring in part.get("coordinates") or [])
                if fill_pattern == "marsh":
                    clip_id = f"marsh-clip-{index}"
                    bbox = polygon_part_mm_bbox(part, transform)
                    lines.append(f'<clipPath id="{clip_id}"><path d="{path}" fill-rule="evenodd"/></clipPath>')
                    if bbox is not None:
                        min_x, min_y, max_x, max_y = bbox
                        pattern_stroke = style.get("pattern_stroke", "#008fd5")
                        pattern_width = float(style.get("pattern_stroke_width_mm", 0.12))
                        pattern_spacing = max(float(style.get("pattern_spacing_mm", 1.0)), 0.1)
                        pattern_dasharray = style.get("pattern_dasharray")
                        dash_attr = f' stroke-dasharray="{pattern_dasharray}"' if pattern_dasharray else ""
                        y = math.floor(min_y / pattern_spacing) * pattern_spacing
                        while y <= max_y + pattern_spacing:
                            lines.append(
                                f'<line x1="{min_x:.3f}" y1="{y:.3f}" x2="{max_x:.3f}" y2="{y:.3f}" '
                                f'stroke="{pattern_stroke}" stroke-width="{pattern_width:.3f}" '
                                f'clip-path="url(#{clip_id})"{dash_attr}/>'
                            )
                            y += pattern_spacing
                else:
                    lines.append(
                        f'<path d="{path}" stroke="{stroke}" fill="{fill}" '
                        f'stroke-width="{stroke_width:.3f}" fill-rule="evenodd"/>'
                    )
            elif part["type"] == "LineString":
                path = svg_path_for_line(part.get("coordinates") or [], transform)
                for layer in line_style_layers(style):
                    layer_stroke = layer.get("stroke", "none")
                    layer_width = float(layer.get("stroke_width_mm", stroke_width))
                    dasharray = layer.get("dasharray")
                    dash_attr = f' stroke-dasharray="{dasharray}"' if dasharray else ""
                    lines.append(
                        f'<path d="{path}" stroke="{layer_stroke}" fill="none" '
                        f'stroke-width="{layer_width:.3f}" stroke-linecap="round" '
                        f'stroke-linejoin="round"{dash_attr}/>'
                    )
            elif part["type"] == "Point":
                x, y = transform.to_mm(part.get("coordinates"))
                label = feature_label_text(feature)
                if label:
                    font_size = float(style.get("font_size_mm", 3.0))
                    font_style = str(style.get("font_style", "normal"))
                    lines.append(
                        f'<text x="{x:.3f}" y="{y:.3f}" text-anchor="middle" '
                        f'font-family="Arial, Helvetica, sans-serif" font-size="{font_size:.3f}" '
                        f'font-style="{font_style}" fill="{fill}">{html.escape(label)}</text>'
                    )
                else:
                    if not should_render_point_symbol(feature):
                        continue
                    radius = point_radius_mm(style)
                    point_fill = fill if fill != "none" else stroke
                    lines.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{radius:.3f}" fill="{point_fill}"/>')
    if include_layout:
        append_svg_layout(
            lines,
            output_path,
            transform,
            map_title_text=map_title_text,
            map_maker=map_maker,
            contour_interval_m=contour_interval_m,
            north_line_spacing_m=north_line_spacing_m,
        )
    lines.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    progress(f"Wrote SVG to {output_path}.")


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


def draw_polygon_marsh_lines(
    canvas: bytearray,
    width: int,
    height: int,
    rings: list[list[tuple[int, int]]],
    color: tuple[int, int, int],
    stroke_px: int,
    spacing_px: int,
    dash_px: int,
    gap_px: int,
) -> None:
    if not rings or not rings[0]:
        return
    xs = [point[0] for ring in rings for point in ring]
    ys = [point[1] for ring in rings for point in ring]
    min_x = max(min(xs), 0)
    max_x = min(max(xs), width - 1)
    min_y = max(min(ys), 0)
    max_y = min(max(ys), height - 1)
    spacing_px = max(spacing_px, 1)
    period = max(dash_px + gap_px, 1)
    radius = max(stroke_px // 2, 0)
    for y in range(min_y - (min_y % spacing_px), max_y + spacing_px + 1, spacing_px):
        for x in range(min_x, max_x + 1):
            if x % period >= dash_px:
                continue
            for oy in range(-radius, radius + 1):
                yy = y + oy
                if yy < min_y or yy > max_y:
                    continue
                if point_in_polygon(x + 0.5, yy + 0.5, rings[0]) and not any(
                    point_in_polygon(x + 0.5, yy + 0.5, hole) for hole in rings[1:]
                ):
                    set_pixel(canvas, width, height, x, yy, color)


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


def render_png_with_pillow(
    geojson: dict[str, Any],
    output_path: Path,
    *,
    transform: RenderTransform,
    dpi: int,
    include_layout: bool,
    north_line_spacing_m: float,
    contour_merge_tolerance_m: float,
) -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False

    features = prepare_render_features(geojson, contour_merge_tolerance_m)
    progress(f"Rendering PNG with Pillow: {len(features)} features at {dpi} dpi...")
    width = max(1, int(round(transform.page_width_mm / 25.4 * dpi)))
    height = max(1, int(round(transform.page_height_mm / 25.4 * dpi)))
    px_per_mm = dpi / 25.4
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    def to_px(coordinate: Any) -> tuple[float, float]:
        x, y = transform.to_mm(coordinate)
        return x * px_per_mm, y * px_per_mm

    for index, feature in enumerate(features, start=1):
        if index % 10000 == 0:
            progress(f"  rendered {index}/{len(features)} features...")
        style = feature_style(feature)
        stroke = color_to_rgb(str(style.get("stroke", "none")))
        fill = color_to_rgb(str(style.get("fill", "none")))
        stroke_px = max(1, int(round(float(style.get("stroke_width_mm", 0.18)) * px_per_mm)))
        fill_pattern = style.get("svg_fill_pattern")
        for part in iter_geometry_parts(feature.get("geometry") or {}):
            if part["type"] == "Polygon":
                rings = [[to_px(point) for point in ring] for ring in part.get("coordinates") or []]
                if not rings:
                    continue
                if fill_pattern == "marsh":
                    xs = [point[0] for ring in rings for point in ring]
                    ys = [point[1] for ring in rings for point in ring]
                    min_x = max(int(math.floor(min(xs))), 0)
                    min_y = max(int(math.floor(min(ys))), 0)
                    max_x = min(int(math.ceil(max(xs))), width - 1)
                    max_y = min(int(math.ceil(max(ys))), height - 1)
                    if max_x < min_x or max_y < min_y:
                        continue
                    local_size = (max_x - min_x + 1, max_y - min_y + 1)
                    local_rings = [[(x - min_x, y - min_y) for x, y in ring] for ring in rings]
                    mask = Image.new("L", local_size, 0)
                    mask_draw = ImageDraw.Draw(mask)
                    mask_draw.polygon(local_rings[0], fill=255)
                    for hole in local_rings[1:]:
                        mask_draw.polygon(hole, fill=0)
                    overlay = Image.new("RGB", local_size, (255, 255, 255))
                    overlay_draw = ImageDraw.Draw(overlay)
                    marsh_color = color_to_rgb(str(style.get("pattern_stroke", "#008fd5"))) or (0, 143, 213)
                    marsh_width = max(1, int(round(float(style.get("pattern_stroke_width_mm", 0.12)) * px_per_mm)))
                    spacing = max(1, int(round(float(style.get("pattern_spacing_mm", 1.0)) * px_per_mm)))
                    dash = max(1, int(round(1.4 * px_per_mm)))
                    gap = max(1, int(round(0.55 * px_per_mm)))
                    for y in range(-(min_y % spacing), local_size[1] + spacing, spacing):
                        for x in range(0, local_size[0], dash + gap):
                            overlay_draw.line([(x, y), (min(x + dash, local_size[0]), y)], fill=marsh_color, width=marsh_width)
                    image.paste(overlay, (min_x, min_y), mask)
                elif fill:
                    draw.polygon(rings[0], fill=fill)
                if stroke:
                    for ring in rings:
                        if len(ring) > 1:
                            draw.line(ring + [ring[0]], fill=stroke, width=stroke_px, joint="curve")
            elif part["type"] == "LineString":
                points = [to_px(point) for point in part.get("coordinates") or []]
                if len(points) < 2:
                    continue
                for layer in line_style_layers(style):
                    layer_stroke = color_to_rgb(str(layer.get("stroke", "none")))
                    if not layer_stroke:
                        continue
                    layer_px = max(1, int(round(float(layer.get("stroke_width_mm", 0.18)) * px_per_mm)))
                    draw.line(points, fill=layer_stroke, width=layer_px, joint="curve")
            elif part["type"] == "Point":
                if feature_label_text(feature):
                    continue
                if not should_render_point_symbol(feature):
                    continue
                color = fill or stroke or (0, 0, 0)
                radius = max(2, int(round(point_radius_mm(style) * px_per_mm)))
                x, y = to_px(part.get("coordinates"))
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    if include_layout:
        purple = (111, 45, 189)
        line_width = max(1, int(round(0.18 * px_per_mm)))
        for x_mm in north_line_x_positions(transform, north_line_spacing_m):
            x = x_mm * px_per_mm
            y0 = transform.margin_mm * px_per_mm
            y1 = (transform.margin_mm + transform.map_height_mm) * px_per_mm
            draw.line([(x, y0), (x, y1)], fill=purple, width=line_width)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    progress(f"Wrote PNG to {output_path}.")
    return True


def render_png(
    geojson: dict[str, Any],
    output_path: Path,
    *,
    transform: RenderTransform,
    dpi: int,
    include_layout: bool = True,
    north_line_spacing_m: float = DEFAULT_NORTH_LINE_SPACING_M,
    contour_merge_tolerance_m: float = DEFAULT_CONTOUR_MERGE_TOLERANCE_M,
) -> None:
    if render_png_with_pillow(
        geojson,
        output_path,
        transform=transform,
        dpi=dpi,
        include_layout=include_layout,
        north_line_spacing_m=north_line_spacing_m,
        contour_merge_tolerance_m=contour_merge_tolerance_m,
    ):
        return
    features = prepare_render_features(geojson, contour_merge_tolerance_m)
    progress(f"Rendering PNG with {len(features)} features at {dpi} dpi...")
    width = max(1, int(round(transform.page_width_mm / 25.4 * dpi)))
    height = max(1, int(round(transform.page_height_mm / 25.4 * dpi)))
    px_per_mm = dpi / 25.4
    canvas = make_canvas(width, height)

    def to_px(coordinate: Any) -> tuple[int, int]:
        x, y = transform.to_mm(coordinate)
        return int(round(x * px_per_mm)), int(round(y * px_per_mm))

    for index, feature in enumerate(features, start=1):
        if index % 10000 == 0:
            progress(f"  rendered {index}/{len(features)} features...")
        style = feature_style(feature)
        stroke = color_to_rgb(str(style.get("stroke", "none")))
        fill = color_to_rgb(str(style.get("fill", "none")))
        stroke_px = max(1, int(round(float(style.get("stroke_width_mm", 0.18)) * px_per_mm)))
        fill_pattern = style.get("svg_fill_pattern")
        for part in iter_geometry_parts(feature.get("geometry") or {}):
            if part["type"] == "Polygon":
                rings = [[to_px(point) for point in ring] for ring in part.get("coordinates") or []]
                if fill_pattern == "marsh":
                    marsh_color = color_to_rgb(str(style.get("pattern_stroke", "#008fd5"))) or (0, 143, 213)
                    marsh_width = max(1, int(round(float(style.get("pattern_stroke_width_mm", 0.12)) * px_per_mm)))
                    marsh_spacing = max(1, int(round(float(style.get("pattern_spacing_mm", 1.0)) * px_per_mm)))
                    draw_polygon_marsh_lines(
                        canvas,
                        width,
                        height,
                        rings,
                        marsh_color,
                        marsh_width,
                        marsh_spacing,
                        max(1, int(round(1.4 * px_per_mm))),
                        max(1, int(round(0.55 * px_per_mm))),
                    )
                elif fill:
                    fill_polygon(canvas, width, height, rings, fill)
                if stroke:
                    for ring in rings:
                        for a, b in zip(ring, ring[1:] + ring[:1]):
                            draw_line(canvas, width, height, a, b, stroke, stroke_px)
            elif part["type"] == "LineString":
                points = [to_px(point) for point in part.get("coordinates") or []]
                for layer in line_style_layers(style):
                    layer_stroke = color_to_rgb(str(layer.get("stroke", "none")))
                    if not layer_stroke:
                        continue
                    layer_px = max(1, int(round(float(layer.get("stroke_width_mm", 0.18)) * px_per_mm)))
                    for a, b in zip(points, points[1:]):
                        draw_line(canvas, width, height, a, b, layer_stroke, layer_px)
            elif part["type"] == "Point":
                if feature_label_text(feature):
                    continue
                if not should_render_point_symbol(feature):
                    continue
                color = fill or stroke or (0, 0, 0)
                radius_px = max(2, int(round(point_radius_mm(style) * px_per_mm)))
                draw_circle(canvas, width, height, to_px(part.get("coordinates")), radius_px, color)
    if include_layout:
        purple = (111, 45, 189)
        for x_mm in north_line_x_positions(transform, north_line_spacing_m):
            x = int(round(x_mm * px_per_mm))
            y0 = int(round(transform.margin_mm * px_per_mm))
            y1 = int(round((transform.margin_mm + transform.map_height_mm) * px_per_mm))
            draw_line(canvas, width, height, (x, y0), (x, y1), purple, max(1, int(round(0.18 * px_per_mm))))
    write_png(output_path, width, height, canvas)
    progress(f"Wrote PNG to {output_path}.")


def pdf_color_operator(color: tuple[int, int, int], stroke: bool) -> str:
    values = " ".join(f"{channel / 255.0:.4f}" for channel in color)
    return values + (" RG" if stroke else " rg")


def pdf_point(transform: RenderTransform, coordinate: Any) -> tuple[float, float]:
    x_mm, y_mm = transform.to_mm(coordinate)
    scale = 72.0 / 25.4
    return x_mm * scale, (transform.page_height_mm - y_mm) * scale


def pdf_mm(x_mm: float, y_mm: float, transform: RenderTransform) -> tuple[float, float]:
    scale = 72.0 / 25.4
    return x_mm * scale, (transform.page_height_mm - y_mm) * scale


def append_pdf_dash(commands: list[str], dasharray: Any) -> None:
    if not dasharray:
        commands.append("[] 0 d")
        return
    scale = 72.0 / 25.4
    values = [float(value) * scale for value in str(dasharray).split()]
    commands.append("[" + " ".join(f"{value:.3f}" for value in values) + "] 0 d")


def pdf_escape_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def append_pdf_text(commands: list[str], x_mm: float, y_mm: float, size_pt: float, text: str, transform: RenderTransform) -> None:
    x, y = pdf_mm(x_mm, y_mm, transform)
    commands.append("0 0 0 rg")
    commands.append("BT")
    commands.append(f"/F1 {size_pt:.2f} Tf")
    commands.append(f"{x:.3f} {y:.3f} Td")
    commands.append(f"({pdf_escape_text(text)}) Tj")
    commands.append("ET")


def append_pdf_label(commands: list[str], feature: dict[str, Any], part: dict[str, Any], style: dict[str, Any], transform: RenderTransform) -> bool:
    label = feature_label_text(feature)
    if not label:
        return False
    fill = color_to_rgb(str(style.get("fill", "#000000"))) or (0, 0, 0)
    x_mm, y_mm = transform.to_mm(part.get("coordinates"))
    x, y = pdf_mm(x_mm, y_mm, transform)
    size_pt = float(style.get("font_size_mm", 3.0)) * 72.0 / 25.4
    commands.append(pdf_color_operator(fill, stroke=False))
    commands.append("BT")
    commands.append(f"/F1 {size_pt:.2f} Tf")
    commands.append(f"{x:.3f} {y:.3f} Td")
    commands.append(f"({pdf_escape_text(label)}) Tj")
    commands.append("ET")
    return True


def append_pdf_circle(commands: list[str], center_x: float, center_y: float, radius: float) -> None:
    kappa = 0.5522847498
    control = radius * kappa
    commands.append(f"{center_x + radius:.3f} {center_y:.3f} m")
    commands.append(
        f"{center_x + radius:.3f} {center_y + control:.3f} "
        f"{center_x + control:.3f} {center_y + radius:.3f} "
        f"{center_x:.3f} {center_y + radius:.3f} c"
    )
    commands.append(
        f"{center_x - control:.3f} {center_y + radius:.3f} "
        f"{center_x - radius:.3f} {center_y + control:.3f} "
        f"{center_x - radius:.3f} {center_y:.3f} c"
    )
    commands.append(
        f"{center_x - radius:.3f} {center_y - control:.3f} "
        f"{center_x - control:.3f} {center_y - radius:.3f} "
        f"{center_x:.3f} {center_y - radius:.3f} c"
    )
    commands.append(
        f"{center_x + control:.3f} {center_y - radius:.3f} "
        f"{center_x + radius:.3f} {center_y - control:.3f} "
        f"{center_x + radius:.3f} {center_y:.3f} c"
    )
    commands.append("h")


def append_pdf_polygon_path(commands: list[str], part: dict[str, Any], transform: RenderTransform) -> None:
    for ring in part.get("coordinates") or []:
        if not ring:
            continue
        x, y = pdf_point(transform, ring[0])
        commands.append(f"{x:.3f} {y:.3f} m")
        for coordinate in ring[1:]:
            x, y = pdf_point(transform, coordinate)
            commands.append(f"{x:.3f} {y:.3f} l")
        commands.append("h")


def append_pdf_marsh_lines(commands: list[str], part: dict[str, Any], style: dict[str, Any], transform: RenderTransform) -> None:
    bbox = polygon_part_mm_bbox(part, transform)
    if bbox is None:
        return
    min_x, min_y, max_x, max_y = bbox
    pattern_stroke = color_to_rgb(str(style.get("pattern_stroke", "#008fd5"))) or (0, 143, 213)
    pattern_width = float(style.get("pattern_stroke_width_mm", 0.12)) * 72.0 / 25.4
    pattern_spacing = max(float(style.get("pattern_spacing_mm", 1.0)), 0.1)
    pattern_dasharray = style.get("pattern_dasharray")
    commands.append("q")
    append_pdf_polygon_path(commands, part, transform)
    commands.append("W n")
    commands.append(pdf_color_operator(pattern_stroke, stroke=True))
    commands.append(f"{pattern_width:.3f} w")
    append_pdf_dash(commands, pattern_dasharray)
    y_mm = math.floor(min_y / pattern_spacing) * pattern_spacing
    while y_mm <= max_y + pattern_spacing:
        x0, y0 = pdf_mm(min_x, y_mm, transform)
        x1, y1 = pdf_mm(max_x, y_mm, transform)
        commands.append(f"{x0:.3f} {y0:.3f} m")
        commands.append(f"{x1:.3f} {y1:.3f} l")
        commands.append("S")
        y_mm += pattern_spacing
    commands.append("Q")


def first_coordinate(coordinates: Any) -> Any | None:
    while isinstance(coordinates, list) and coordinates:
        candidate = coordinates[0]
        if isinstance(candidate, list) and len(candidate) >= 2 and all(isinstance(value, (int, float)) for value in candidate[:2]):
            return candidate
        coordinates = candidate
    return None


def feature_label_position_mm(feature: dict[str, Any], transform: RenderTransform) -> tuple[float, float] | None:
    geometry = feature.get("geometry") or {}
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        return transform.to_mm(coordinates)
    part = next(iter(iter_geometry_parts(geometry)), None)
    if part is None:
        coordinate = first_coordinate(coordinates)
        return transform.to_mm(coordinate) if coordinate is not None else None
    if part["type"] == "Polygon":
        bbox = polygon_part_mm_bbox(part, transform)
        if bbox is None:
            return None
        min_x, min_y, max_x, max_y = bbox
        return (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    if part["type"] == "LineString":
        line = part.get("coordinates") or []
        if not line:
            return None
        return transform.to_mm(line[len(line) // 2])
    if part["type"] == "Point":
        return transform.to_mm(part.get("coordinates"))
    return None


def feature_iof_symbol_number(feature: dict[str, Any]) -> str | None:
    properties = feature.get("properties") or {}
    number = properties.get("iof_symbol_number")
    if number:
        return str(number)
    symbol = properties.get("symbol")
    if isinstance(symbol, (str, int)) and str(symbol).isdigit():
        return str(symbol)
    metadata = iof_symbol_metadata(feature_symbol(feature))
    return metadata["iof_symbol_number"] if metadata else None


def append_pdf_symbol_number_labels(commands: list[str], features: list[dict[str, Any]], transform: RenderTransform) -> None:
    commands.append("0 0 0 rg")
    for feature in features:
        number = feature_iof_symbol_number(feature)
        position = feature_label_position_mm(feature, transform)
        if not number or position is None:
            continue
        x_mm, y_mm = position
        x, y = pdf_mm(x_mm, y_mm, transform)
        commands.append("BT")
        commands.append("/F1 5.00 Tf")
        commands.append(f"{x:.3f} {y:.3f} Td")
        commands.append(f"({pdf_escape_text(number)}) Tj")
        commands.append("ET")


def append_pdf_layout(
    commands: list[str],
    output_path: Path,
    transform: RenderTransform,
    *,
    map_title_text: str | None,
    map_maker: str,
    contour_interval_m: float,
    north_line_spacing_m: float,
) -> None:
    purple = (111, 45, 189)
    commands.append(pdf_color_operator(purple, stroke=True))
    commands.append(f"{0.18 * 72.0 / 25.4:.3f} w")
    for x_mm in north_line_x_positions(transform, north_line_spacing_m):
        x0, y0 = pdf_mm(x_mm, transform.margin_mm, transform)
        x1, y1 = pdf_mm(x_mm, transform.margin_mm + transform.map_height_mm, transform)
        commands.append(f"{x0:.3f} {y0:.3f} m")
        commands.append(f"{x1:.3f} {y1:.3f} l")
        commands.append("S")
    commands.append("0 0 0 RG")
    commands.append(f"{0.12 * 72.0 / 25.4:.3f} w")
    x, y = pdf_mm(transform.margin_mm, transform.margin_mm + transform.map_height_mm, transform)
    commands.append(
        f"{x:.3f} {y:.3f} {transform.map_width_mm * 72.0 / 25.4:.3f} "
        f"{transform.map_height_mm * 72.0 / 25.4:.3f} re"
    )
    commands.append("S")
    append_pdf_text(commands, transform.margin_mm, max(transform.margin_mm - 1.2, 3.0), 9.0, map_title(output_path, map_title_text), transform)
    append_pdf_text(
        commands,
        transform.margin_mm,
        transform.page_height_mm - 1.5,
        7.5,
        map_footer_text(transform, map_maker, contour_interval_m),
        transform,
    )
    append_pdf_text(commands, transform.page_width_mm - transform.margin_mm - 4.0, max(transform.margin_mm - 1.0, 4.5), 9.0, "N", transform)


def render_pdf(
    geojson: dict[str, Any],
    output_path: Path,
    *,
    transform: RenderTransform,
    include_layout: bool = True,
    map_title_text: str | None = None,
    map_maker: str = "mml-omap",
    contour_interval_m: float = 5.0,
    north_line_spacing_m: float = DEFAULT_NORTH_LINE_SPACING_M,
    contour_merge_tolerance_m: float = DEFAULT_CONTOUR_MERGE_TOLERANCE_M,
    include_symbol_numbers: bool = False,
) -> None:
    features = prepare_render_features(geojson, contour_merge_tolerance_m)
    progress(f"Rendering PDF with {len(features)} features...")
    page_width = transform.page_width_mm * 72.0 / 25.4
    page_height = transform.page_height_mm * 72.0 / 25.4
    commands = ["1 1 1 rg", f"0 0 {page_width:.3f} {page_height:.3f} re", "f"]
    for index, feature in enumerate(features, start=1):
        if index % 10000 == 0:
            progress(f"  rendered {index}/{len(features)} features...")
        style = feature_style(feature)
        stroke = color_to_rgb(str(style.get("stroke", "none")))
        fill = color_to_rgb(str(style.get("fill", "none")))
        stroke_width = float(style.get("stroke_width_mm", 0.18)) * 72.0 / 25.4
        fill_pattern = style.get("svg_fill_pattern")
        for part in iter_geometry_parts(feature.get("geometry") or {}):
            if part["type"] == "Polygon":
                if fill_pattern == "marsh":
                    append_pdf_marsh_lines(commands, part, style, transform)
                    continue
                if not fill and not stroke:
                    continue
                if fill:
                    commands.append(pdf_color_operator(fill, stroke=False))
                if stroke:
                    commands.append(pdf_color_operator(stroke, stroke=True))
                    commands.append(f"{stroke_width:.3f} w")
                append_pdf_polygon_path(commands, part, transform)
                commands.append("B" if fill and stroke else ("f" if fill else "S"))
            elif part["type"] == "LineString":
                line = part.get("coordinates") or []
                if len(line) < 2:
                    continue
                for layer in line_style_layers(style):
                    layer_stroke = color_to_rgb(str(layer.get("stroke", "none")))
                    if not layer_stroke:
                        continue
                    layer_width = float(layer.get("stroke_width_mm", 0.18)) * 72.0 / 25.4
                    commands.append(pdf_color_operator(layer_stroke, stroke=True))
                    commands.append(f"{layer_width:.3f} w")
                    append_pdf_dash(commands, layer.get("dasharray"))
                    x, y = pdf_point(transform, line[0])
                    commands.append(f"{x:.3f} {y:.3f} m")
                    for coordinate in line[1:]:
                        x, y = pdf_point(transform, coordinate)
                        commands.append(f"{x:.3f} {y:.3f} l")
                    commands.append("S")
            elif part["type"] == "Point":
                if append_pdf_label(commands, feature, part, style, transform):
                    continue
                if not should_render_point_symbol(feature):
                    continue
                color = fill or stroke or (0, 0, 0)
                commands.append(pdf_color_operator(color, stroke=False))
                x, y = pdf_point(transform, part.get("coordinates"))
                radius = max(point_radius_mm(style) * 72.0 / 25.4, 1.0)
                append_pdf_circle(commands, x, y, radius)
                commands.append("f")
    if include_layout:
        append_pdf_layout(
            commands,
            output_path,
            transform,
            map_title_text=map_title_text,
            map_maker=map_maker,
            contour_interval_m=contour_interval_m,
            north_line_spacing_m=north_line_spacing_m,
        )
    if include_symbol_numbers:
        append_pdf_symbol_number_labels(commands, features, transform)
    content = ("\n".join(commands) + "\n").encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width:.3f} {page_height:.3f}] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            f"/Encoding /WinAnsiEncoding >> >> >> "
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
    progress(f"Wrote PDF to {output_path}.")


def command_download(args: argparse.Namespace) -> int:
    api_key = resolve_api_key(args)
    bbox = parse_orienteering_bbox(args.bbox)
    magnetic_declination_deg = resolve_magnetic_declination_deg(
        args.magnetic_declination_deg,
        bbox=bbox,
        magnetic_date=args.magnetic_date,
    )
    mml_bbox = enclosing_grid_bbox(bbox, magnetic_declination_deg)
    progress(
        "Submitting MML bbox job "
        f"for paper bbox {format_bbox(bbox)}; fetch bbox {format_bbox(mml_bbox)}; "
        f"KOK {magnetic_declination_deg:.2f} deg."
    )
    results = mml_job_results_with_retries(
        submit_job=lambda: submit_mml_bbox_job(
            api_key=api_key,
            bbox=mml_bbox,
            theme=args.theme,
            base_url=args.base_url.rstrip("/"),
        ),
        api_key=api_key,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
        description="vector bbox",
    )
    download_url = pick_download_url(results)
    download_file(download_url, api_key, Path(args.output))
    return 0


def download_map_sheet_process_file(
    *,
    api_key: str,
    map_sheets: list[str],
    process_id: str,
    file_format: str,
    output_path: Path,
    base_url: str,
    poll_seconds: float,
    timeout_seconds: float,
    suffixes: tuple[str, ...],
    extra_inputs: dict[str, Any] | None = None,
) -> Path:
    results = mml_job_results_with_retries(
        submit_job=lambda: submit_map_sheet_process_job(
            api_key=api_key,
            map_sheets=map_sheets,
            process_id=process_id,
            file_format=file_format,
            base_url=base_url.rstrip("/"),
            extra_inputs=extra_inputs,
        ),
        api_key=api_key,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        description=process_id,
    )
    download_url = pick_download_url_by_suffix(results, suffixes)
    download_file(download_url, api_key, output_path)
    return output_path


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
    rules = table_rules_with_optional_forest_mask(
        load_table_rules(Path(args.mapping) if args.mapping else None),
        include_forest_mask=args.include_forest_mask,
    )
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


def download_args_for_generate(args: argparse.Namespace, archive_path: Path) -> argparse.Namespace:
    download_vars = vars(args).copy()
    download_vars["output"] = str(archive_path)
    return argparse.Namespace(**download_vars)


def command_generate(args: argparse.Namespace) -> int:
    api_key = resolve_api_key(args)
    output = Path(args.output)
    work_dir = Path(args.work_dir)
    archive_path = work_dir / (output.stem + ".zip")
    progress(f"Generating {output}...")
    command_download(download_args_for_generate(args, archive_path))
    progress(f"Extracting GeoPackage from {archive_path}...")
    gpkg_path = extract_first_gpkg(archive_path, work_dir / output.stem)
    rules = table_rules_with_optional_forest_mask(
        load_table_rules(Path(args.mapping) if args.mapping else None),
        include_forest_mask=args.include_forest_mask,
    )
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
    progress(f"Writing GeoJSON to {output}...")
    write_json(output, geojson)
    print(f"Wrote {len(geojson['features'])} features from {gpkg_path}.")
    return 0


def build_source_data(args: argparse.Namespace) -> dict[str, Any]:
    api_key = resolve_api_key(args)
    output_base = Path(args.output)
    work_dir = Path(args.work_dir) if args.work_dir else output_base.parent / "downloads"
    bbox = parse_orienteering_bbox(args.bbox)
    magnetic_date = parse_date(args.magnetic_date)
    magnetic_declination_deg = resolve_magnetic_declination_deg(
        args.magnetic_declination_deg,
        bbox=bbox,
        magnetic_date=magnetic_date.isoformat(),
    )
    archive_path = work_dir / "mml" / (output_base.stem + ".zip")
    progress(f"Downloading MML vector data to {archive_path}...")
    command_download(download_args_for_generate(args, archive_path))
    gpkg_path = extract_first_gpkg(archive_path, work_dir / "mml" / output_base.stem)
    terrain_bbox = expand_bbox(enclosing_grid_bbox(bbox, magnetic_declination_deg), args.terrain_context_margin_m)
    laser_sheets = tm35_map_sheets_for_bbox(terrain_bbox)
    progress(f"Downloading MML laser scanning sheets: {', '.join(laser_sheets)}")
    laser_archive_path = work_dir / "mml" / f"{output_base.stem}-laser.zip"
    download_map_sheet_process_file(
        api_key=api_key,
        map_sheets=laser_sheets,
        process_id="laserkeilausaineisto_05_karttalehti",
        file_format="LAZ",
        output_path=laser_archive_path,
        base_url=args.base_url,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
        suffixes=(".laz", ".zip"),
        extra_inputs={"dataSetInput": "Uusin"},
    )
    laser_dir = work_dir / "mml" / f"{output_base.stem}-laser"
    laser_paths = extract_laser_paths(laser_archive_path, laser_dir)
    vector_rules = table_rules_with_optional_forest_mask(
        load_table_rules(Path(args.mapping) if args.mapping else None),
        include_forest_mask=False,
    )
    vector_geojson = convert_gpkg_to_geojson(
        gpkg_path,
        bbox=enclosing_grid_bbox(bbox, magnetic_declination_deg),
        clip_frame=OrientedFrame(bbox, magnetic_declination_deg),
        table_rules=vector_rules,
        include_unmapped=False,
        map_frame={
            "bbox": bbox,
            "magnetic_declination_deg": magnetic_declination_deg,
            "magnetic_date": magnetic_date.isoformat(),
        },
    )
    lidar_rows = read_lidar_point_rows_many(laser_paths)
    xs, ys, elevation_points, ground_report = ground_grid_from_lidar_points(
        lidar_rows,
        bbox=terrain_bbox,
        cell_size_m=args.ground_cell_size_m,
        ground_quantile=args.ground_quantile,
        smoothing_sigma_m=args.ground_smoothing_sigma_m,
    )
    return {
        "bbox": bbox,
        "terrain_bbox": terrain_bbox,
        "magnetic_declination_deg": magnetic_declination_deg,
        "magnetic_date": magnetic_date.isoformat(),
        "vector_geojson": vector_geojson,
        "lidar_rows": lidar_rows,
        "laser_sheets": laser_sheets,
        "laser_paths": [str(path) for path in laser_paths],
        "xs": xs,
        "ys": ys,
        "elevation_points": elevation_points,
        "ground_report": ground_report,
    }


def combined_map_from_source_data(source_data: dict[str, Any], args: argparse.Namespace, *, interval_m: float) -> tuple[dict[str, Any], dict[str, Any]]:
    xs = source_data["xs"]
    ys = source_data["ys"]
    elevation_points = source_data["elevation_points"]
    contour_features = contour_features_from_xyz_grid(
        xs,
        ys,
        elevation_points,
        interval_m=interval_m,
        simplify_tolerance_m=getattr(args, "contour_simplify_tolerance_m", 0.0),
        index_contour_every=getattr(args, "index_contour_every", 5),
    )
    cliff_features = cliff_features_from_xyz_grid(
        xs,
        ys,
        elevation_points,
        slope_threshold_deg=args.slope_threshold_deg,
        min_length_m=args.min_cliff_length_m,
    )
    vegetation_features = lidar_vegetation_features(
        source_data["lidar_rows"],
        cell_size_m=args.cell_size_m,
        min_height_m=args.min_height_m,
        slow_count=args.slow_count,
        fight_count=args.fight_count,
    )
    terrain_geojson = combined_terrain_geojson(
        contour_features=contour_features,
        cliff_features=cliff_features,
        vegetation_features=vegetation_features,
    )
    terrain_geojson = add_map_frame_if_requested(
        terrain_geojson,
        bbox_raw=args.bbox,
        magnetic_declination_deg_raw=str(source_data["magnetic_declination_deg"]),
        magnetic_date_raw=source_data["magnetic_date"],
    )
    combined = {
        "type": "FeatureCollection",
        "name": "mml-omap-combined",
        "crs": {"type": "name", "properties": {"name": "EPSG:3067"}},
        "map_frame": {
            "bbox": source_data["bbox"],
            "magnetic_declination_deg": source_data["magnetic_declination_deg"],
            "magnetic_date": source_data["magnetic_date"],
        },
        "features": [*geojson_features(source_data["vector_geojson"]), *geojson_features(terrain_geojson)],
    }
    report = {
        "bbox": source_data["bbox"],
        "terrain_bbox": source_data["terrain_bbox"],
        "magnetic_declination_deg": source_data["magnetic_declination_deg"],
        "magnetic_date": source_data["magnetic_date"],
        "laser_sheets": source_data["laser_sheets"],
        "laser_paths": source_data["laser_paths"],
        "contour_interval_m": interval_m,
        "contour_feature_count": len(contour_features),
        "cliff_feature_count": len(cliff_features),
        "vegetation_feature_count": len(vegetation_features),
        "vector_feature_count": len(source_data["vector_geojson"]["features"]),
        "ground_model": source_data["ground_report"],
    }
    progress(
        "Combined "
        f"{len(source_data['vector_geojson']['features'])} vector, "
        f"{len(contour_features)} contour, "
        f"{len(cliff_features)} cliff, "
        f"{len(vegetation_features)} vegetation features."
    )
    return combined, report


def build_combined_map(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    source_data = build_source_data(args)
    return combined_map_from_source_data(source_data, args, interval_m=args.interval_m)


def command_build(args: argparse.Namespace) -> int:
    output_base = render_output_base(args.output)
    combined, report = build_combined_map(args)
    render_build_outputs(output_base, combined, report, args)
    return 0


def render_build_outputs(
    output_base: Path,
    combined: dict[str, Any],
    report: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    geojson_path = output_base.with_suffix(".geojson")
    write_json(geojson_path, combined)
    write_json(output_base.with_name(output_base.name + "-terrain-report").with_suffix(".json"), report)
    transform = make_render_transform(
        argparse.Namespace(
            bbox=None,
            magnetic_declination_deg="auto",
            magnetic_date=args.magnetic_date,
            scale=args.scale,
            margin_mm=args.margin_mm,
        ),
        combined,
    )
    contour_interval_m = resolve_contour_interval_m("auto", combined)
    render_png(
        combined,
        output_base.with_suffix(".png"),
        transform=transform,
        dpi=args.dpi,
        include_layout=True,
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
    )
    render_pdf(
        combined,
        output_base.with_suffix(".pdf"),
        transform=transform,
        include_layout=True,
        map_title_text=args.map_title,
        map_maker=args.map_maker,
        contour_interval_m=contour_interval_m,
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
    )
    render_pdf(
        combined,
        output_base.with_name(output_base.name + "-symbols").with_suffix(".pdf"),
        transform=transform,
        include_layout=True,
        map_title_text=args.map_title,
        map_maker=args.map_maker,
        contour_interval_m=contour_interval_m,
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
        include_symbol_numbers=True,
    )
    print(f"Wrote {geojson_path}, {output_base.with_suffix('.png')}, {output_base.with_suffix('.pdf')}.")


def command_ekp(args: argparse.Namespace) -> int:
    build_args = argparse.Namespace(
        output="builds/examples/espoo-keskuspuisto/mapant-center",
        bbox="371255,6673869,373305,6675299",
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
        poll_seconds=args.poll_seconds,
        timeout_seconds=args.timeout_seconds,
        theme="maastotietokanta_kaikki",
        work_dir=None,
        mapping=None,
        scale=5000,
        margin_mm=5.0,
        dpi=300,
        map_title="Espoon keskuspuisto",
        map_maker="mml-omap",
        interval_m=2.5,
        contour_simplify_tolerance_m=0.0,
        index_contour_every=5,
        ground_cell_size_m=1.0,
        ground_quantile=0.5,
        ground_smoothing_sigma_m=1.5,
        terrain_context_margin_m=DEFAULT_TERRAIN_CONTEXT_MARGIN_M,
        slope_threshold_deg=38.0,
        min_cliff_length_m=8.0,
        cell_size_m=4.0,
        min_height_m=1.8,
        slow_count=4,
        fight_count=12,
        north_line_spacing_m=DEFAULT_NORTH_LINE_SPACING_M,
        contour_merge_tolerance_m=DEFAULT_CONTOUR_MERGE_TOLERANCE_M,
        magnetic_declination_deg="auto",
        magnetic_date=None,
    )
    source_data = build_source_data(build_args)
    for interval_m in (1.0, 2.5, 5.0):
        combined, report = combined_map_from_source_data(source_data, build_args, interval_m=interval_m)
        output_base = render_output_base(build_args.output).with_name(
            f"{render_output_base(build_args.output).name}-{contour_interval_slug(interval_m)}"
        )
        render_build_outputs(output_base, combined, report, build_args)
    return 0


def contour_interval_slug(interval_m: float) -> str:
    if float(interval_m).is_integer():
        return f"{int(interval_m)}m"
    return f"{str(interval_m).replace('.', '_')}m"


def command_contours_from_xyz(args: argparse.Namespace) -> int:
    xs, ys, points = read_xyz_grid(Path(args.input))
    features = contour_features_from_xyz_grid(
        xs,
        ys,
        points,
        interval_m=args.interval_m,
        min_level=args.min_level,
        max_level=args.max_level,
        simplify_tolerance_m=getattr(args, "contour_simplify_tolerance_m", 0.0),
        index_contour_every=getattr(args, "index_contour_every", 5),
    )
    geojson: dict[str, Any] = {
        "type": "FeatureCollection",
        "name": "mml-omap-lidar-contours",
        "crs": {"type": "name", "properties": {"name": "EPSG:3067"}},
        "features": features,
    }
    if args.bbox:
        bbox = parse_orienteering_bbox(args.bbox)
        magnetic_date = parse_date(args.magnetic_date)
        magnetic_declination_deg = resolve_magnetic_declination_deg(
            args.magnetic_declination_deg,
            bbox=bbox,
            magnetic_date=magnetic_date.isoformat(),
        )
        geojson = clip_geojson_to_frame(geojson, OrientedFrame(bbox, magnetic_declination_deg))
        geojson["map_frame"] = {
            "bbox": bbox,
            "magnetic_declination_deg": magnetic_declination_deg,
            "magnetic_date": magnetic_date.isoformat(),
        }
    write_json(Path(args.output), geojson)
    print(f"Wrote {len(geojson['features'])} LiDAR-derived contour features.")
    return 0


def command_cliffs_from_xyz(args: argparse.Namespace) -> int:
    xs, ys, points = read_xyz_grid(Path(args.input))
    features = cliff_features_from_xyz_grid(
        xs,
        ys,
        points,
        slope_threshold_deg=args.slope_threshold_deg,
        min_length_m=args.min_length_m,
    )
    geojson: dict[str, Any] = {
        "type": "FeatureCollection",
        "name": "mml-omap-lidar-cliffs",
        "crs": {"type": "name", "properties": {"name": "EPSG:3067"}},
        "features": features,
    }
    write_json(Path(args.output), geojson)
    print(f"Wrote {len(features)} LiDAR-derived cliff features.")
    return 0


def command_vegetation_from_lidar(args: argparse.Namespace) -> int:
    rows = read_lidar_point_rows(Path(args.input))
    features = lidar_vegetation_features(
        rows,
        cell_size_m=args.cell_size_m,
        min_height_m=args.min_height_m,
        slow_count=args.slow_count,
        fight_count=args.fight_count,
    )
    geojson: dict[str, Any] = {
        "type": "FeatureCollection",
        "name": "mml-omap-lidar-vegetation",
        "crs": {"type": "name", "properties": {"name": "EPSG:3067"}},
        "features": features,
    }
    if args.bbox:
        bbox = parse_orienteering_bbox(args.bbox)
        magnetic_date = parse_date(args.magnetic_date)
        magnetic_declination_deg = resolve_magnetic_declination_deg(
            args.magnetic_declination_deg,
            bbox=bbox,
            magnetic_date=magnetic_date.isoformat(),
        )
        geojson = clip_geojson_to_frame(geojson, OrientedFrame(bbox, magnetic_declination_deg))
        geojson["map_frame"] = {
            "bbox": bbox,
            "magnetic_declination_deg": magnetic_declination_deg,
            "magnetic_date": magnetic_date.isoformat(),
        }
    write_json(Path(args.output), geojson)
    print(f"Wrote {len(geojson['features'])} LiDAR-derived vegetation features.")
    return 0


def command_terrain_from_lidar(args: argparse.Namespace) -> int:
    xs, ys, points = read_xyz_grid(Path(args.xyz))
    contour_features = contour_features_from_xyz_grid(
        xs,
        ys,
        points,
        interval_m=args.interval_m,
        simplify_tolerance_m=getattr(args, "contour_simplify_tolerance_m", 0.0),
        index_contour_every=getattr(args, "index_contour_every", 5),
    )
    cliff_features = cliff_features_from_xyz_grid(
        xs,
        ys,
        points,
        slope_threshold_deg=args.slope_threshold_deg,
        min_length_m=args.min_cliff_length_m,
    )
    vegetation_features: list[dict[str, Any]] = []
    if args.points:
        vegetation_features = lidar_vegetation_features(
            read_lidar_point_rows(Path(args.points)),
            cell_size_m=args.cell_size_m,
            min_height_m=args.min_height_m,
            slow_count=args.slow_count,
            fight_count=args.fight_count,
        )
    geojson = combined_terrain_geojson(
        contour_features=contour_features,
        cliff_features=cliff_features,
        vegetation_features=vegetation_features,
    )
    geojson = add_map_frame_if_requested(
        geojson,
        bbox_raw=args.bbox,
        magnetic_declination_deg_raw=args.magnetic_declination_deg,
        magnetic_date_raw=args.magnetic_date,
    )
    write_json(Path(args.output), geojson)
    print(
        "Wrote "
        f"{len(contour_features)} contours, "
        f"{len(cliff_features)} cliffs, "
        f"{len(vegetation_features)} vegetation features."
    )
    return 0


def merge_geojson_documents(paths: list[Path]) -> dict[str, Any]:
    if not paths:
        raise ValueError("At least one input GeoJSON is required")
    merged_features: list[dict[str, Any]] = []
    output: dict[str, Any] = {
        "type": "FeatureCollection",
        "name": "mml-omap-merged",
        "crs": {"type": "name", "properties": {"name": "EPSG:3067"}},
        "features": merged_features,
    }
    for path in paths:
        geojson = read_json(path)
        if "map_frame" in geojson and "map_frame" not in output:
            output["map_frame"] = geojson["map_frame"]
        merged_features.extend(geojson_features(geojson))
    return output


def command_merge_geojson(args: argparse.Namespace) -> int:
    geojson = merge_geojson_documents([Path(path) for path in args.inputs])
    write_json(Path(args.output), geojson)
    print(f"Wrote {len(geojson['features'])} merged features.")
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
    clipped_geojson = clip_geojson_for_render(args, geojson, transform)
    render_svg(
        clipped_geojson,
        Path(args.output),
        transform=transform,
        include_layout=not args.no_layout,
        map_title_text=args.map_title,
        map_maker=args.map_maker,
        contour_interval_m=resolve_contour_interval_m(args.contour_interval_m, clipped_geojson),
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
    )
    return 0


def command_render_png(args: argparse.Namespace) -> int:
    geojson = read_json(Path(args.input))
    transform = make_render_transform(args, geojson)
    clipped_geojson = clip_geojson_for_render(args, geojson, transform)
    render_png(
        clipped_geojson,
        Path(args.output),
        transform=transform,
        dpi=args.dpi,
        include_layout=not args.no_layout,
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
    )
    return 0


def command_render_pdf(args: argparse.Namespace) -> int:
    geojson = read_json(Path(args.input))
    transform = make_render_transform(args, geojson)
    clipped_geojson = clip_geojson_for_render(args, geojson, transform)
    render_pdf(
        clipped_geojson,
        Path(args.output),
        transform=transform,
        include_layout=not args.no_layout,
        map_title_text=args.map_title,
        map_maker=args.map_maker,
        contour_interval_m=resolve_contour_interval_m(args.contour_interval_m, clipped_geojson),
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
        include_symbol_numbers=getattr(args, "symbol_numbers", False),
    )
    return 0


def render_output_base(output: str) -> Path:
    path = Path(output)
    return path.with_suffix("") if path.suffix.lower() in {".png", ".pdf", ".svg"} else path


def command_render(args: argparse.Namespace) -> int:
    geojson = read_json(Path(args.input))
    transform = make_render_transform(args, geojson)
    clipped_geojson = clip_geojson_for_render(args, geojson, transform)
    base = render_output_base(args.output)
    contour_interval_m = resolve_contour_interval_m(args.contour_interval_m, clipped_geojson)
    render_png(
        clipped_geojson,
        base.with_suffix(".png"),
        transform=transform,
        dpi=args.dpi,
        include_layout=not args.no_layout,
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
    )
    render_pdf(
        clipped_geojson,
        base.with_suffix(".pdf"),
        transform=transform,
        include_layout=not args.no_layout,
        map_title_text=args.map_title,
        map_maker=args.map_maker,
        contour_interval_m=contour_interval_m,
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
    )
    render_pdf(
        clipped_geojson,
        base.with_name(base.name + "-symbols").with_suffix(".pdf"),
        transform=transform,
        include_layout=not args.no_layout,
        map_title_text=args.map_title,
        map_maker=args.map_maker,
        contour_interval_m=contour_interval_m,
        north_line_spacing_m=args.north_line_spacing_m,
        contour_merge_tolerance_m=args.contour_merge_tolerance_m,
        include_symbol_numbers=True,
    )
    return 0


def command_symbols(args: argparse.Namespace) -> int:
    symbol_library = export_symbol_library()
    if args.output:
        write_json(Path(args.output), symbol_library)
    else:
        json.dump(symbol_library, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
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

    build = subparsers.add_parser(
        "build",
        parents=[common_api],
        help="Build the combined orienteering map from MML vectors, a ground XYZ grid, and LiDAR points.",
    )
    build.add_argument("output", help="Output base path. Writes .geojson, .png, .pdf, and -symbols.pdf.")
    build.add_argument("bbox", help="min_x,min_y,max_x,max_y in EPSG:3067 meters")
    build.add_argument("--theme", default="maastotietokanta_kaikki")
    build.add_argument("--work-dir")
    build.add_argument("--mapping", help="JSON table mapping overrides")
    build.add_argument("--scale", type=int, default=5000)
    build.add_argument("--margin-mm", type=float, default=5.0)
    build.add_argument("--dpi", type=int, default=300)
    build.add_argument("--map-title", help="Title text printed in SVG/PDF layout metadata.")
    build.add_argument("--map-maker", default="mml-omap", help="Map maker text printed in SVG/PDF layout metadata.")
    build.add_argument("--interval-m", type=float, default=2.5)
    build.add_argument("--contour-simplify-tolerance-m", type=float, default=0.0)
    build.add_argument("--index-contour-every", type=int, default=5)
    build.add_argument("--ground-cell-size-m", type=float, default=1.0)
    build.add_argument("--ground-quantile", type=float, default=0.5)
    build.add_argument("--ground-smoothing-sigma-m", type=float, default=1.5)
    build.add_argument("--terrain-context-margin-m", type=float, default=DEFAULT_TERRAIN_CONTEXT_MARGIN_M)
    build.add_argument("--slope-threshold-deg", type=float, default=38.0)
    build.add_argument("--min-cliff-length-m", type=float, default=8.0)
    build.add_argument("--cell-size-m", type=float, default=4.0)
    build.add_argument("--min-height-m", type=float, default=1.8)
    build.add_argument("--slow-count", type=int, default=4)
    build.add_argument("--fight-count", type=int, default=12)
    build.add_argument("--north-line-spacing-m", type=float, default=DEFAULT_NORTH_LINE_SPACING_M)
    build.add_argument("--contour-merge-tolerance-m", type=float, default=DEFAULT_CONTOUR_MERGE_TOLERANCE_M)
    build.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK.",
    )
    build.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")
    build.set_defaults(func=command_build)

    ekp = subparsers.add_parser(
        "ekp",
        parents=[common_api],
        help="Build the Espoon keskuspuisto example.",
    )
    ekp.set_defaults(func=command_ekp)

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
    convert.add_argument(
        "--include-forest-mask",
        action="store_true",
        help="Map MML metsamaankasvillisuus polygons to ISOM 406 as a rough green forest proxy.",
    )
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
    generate.add_argument(
        "--include-forest-mask",
        action="store_true",
        help="Map MML metsamaankasvillisuus polygons to ISOM 406 as a rough green forest proxy.",
    )
    generate.set_defaults(func=command_generate)

    contours = subparsers.add_parser(
        "contours-from-xyz",
        help="Create ISOM contour GeoJSON from a regular ground-elevation XYZ grid.",
    )
    contours.add_argument("input", help="Whitespace- or comma-separated XYZ grid in EPSG:3067 meters.")
    contours.add_argument("output")
    contours.add_argument("--interval-m", type=float, default=2.5, help="Contour interval in meters.")
    contours.add_argument("--min-level", type=float, help="Optional lowest contour elevation in meters.")
    contours.add_argument("--max-level", type=float, help="Optional highest contour elevation in meters.")
    contours.add_argument("--contour-simplify-tolerance-m", type=float, default=0.0)
    contours.add_argument("--index-contour-every", type=int, default=5)
    contours.add_argument("--bbox", help="Optional min_x,min_y,max_x,max_y clip in EPSG:3067 meters")
    contours.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK. Used when --bbox is set.",
    )
    contours.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")
    contours.set_defaults(func=command_contours_from_xyz)

    cliffs = subparsers.add_parser(
        "cliffs-from-xyz",
        help="Create candidate ISOM cliff GeoJSON from a regular ground-elevation XYZ grid.",
    )
    cliffs.add_argument("input", help="Whitespace- or comma-separated XYZ grid in EPSG:3067 meters.")
    cliffs.add_argument("output")
    cliffs.add_argument("--slope-threshold-deg", type=float, default=38.0)
    cliffs.add_argument("--min-length-m", type=float, default=8.0)
    cliffs.set_defaults(func=command_cliffs_from_xyz)

    vegetation = subparsers.add_parser(
        "vegetation-from-lidar",
        help="Create candidate ISOM vegetation GeoJSON from LAS/LAZ or x y z classification point rows.",
    )
    vegetation.add_argument("input", help="LAS/LAZ or text rows: x y z [classification].")
    vegetation.add_argument("output")
    vegetation.add_argument("--cell-size-m", type=float, default=4.0)
    vegetation.add_argument("--min-height-m", type=float, default=1.8)
    vegetation.add_argument("--slow-count", type=int, default=4)
    vegetation.add_argument("--fight-count", type=int, default=12)
    vegetation.add_argument("--bbox", help="Optional min_x,min_y,max_x,max_y clip in EPSG:3067 meters")
    vegetation.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK. Used when --bbox is set.",
    )
    vegetation.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")
    vegetation.set_defaults(func=command_vegetation_from_lidar)

    terrain = subparsers.add_parser(
        "terrain-from-lidar",
        help="Create combined contours, candidate cliffs, and candidate vegetation from LiDAR-derived inputs.",
    )
    terrain.add_argument("--xyz", required=True, help="Regular ground-elevation XYZ grid in EPSG:3067 meters.")
    terrain.add_argument("--points", help="Optional LAS/LAZ or text rows for vegetation: x y z [classification].")
    terrain.add_argument("output")
    terrain.add_argument("--interval-m", type=float, default=2.5)
    terrain.add_argument("--contour-simplify-tolerance-m", type=float, default=0.0)
    terrain.add_argument("--index-contour-every", type=int, default=5)
    terrain.add_argument("--slope-threshold-deg", type=float, default=38.0)
    terrain.add_argument("--min-cliff-length-m", type=float, default=8.0)
    terrain.add_argument("--cell-size-m", type=float, default=4.0)
    terrain.add_argument("--min-height-m", type=float, default=1.8)
    terrain.add_argument("--slow-count", type=int, default=4)
    terrain.add_argument("--fight-count", type=int, default=12)
    terrain.add_argument("--bbox", help="Optional min_x,min_y,max_x,max_y clip in EPSG:3067 meters")
    terrain.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK. Used when --bbox is set.",
    )
    terrain.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")
    terrain.set_defaults(func=command_terrain_from_lidar)

    merge = subparsers.add_parser("merge-geojson", help="Merge multiple GeoJSON inputs into one FeatureCollection.")
    merge.add_argument("output")
    merge.add_argument("inputs", nargs="+")
    merge.set_defaults(func=command_merge_geojson)

    common_render = argparse.ArgumentParser(add_help=False)
    common_render.add_argument("input", help="Input GeoJSON")
    common_render.add_argument("output")
    common_render.add_argument("--bbox", help="Optional render bounds as min_x,min_y,max_x,max_y")
    common_render.add_argument("--scale", type=int, default=10000)
    common_render.add_argument("--margin-mm", type=float, default=5.0)
    common_render.add_argument("--map-title", help="Title text printed in SVG/PDF layout metadata.")
    common_render.add_argument("--map-maker", default="mml-omap", help="Map maker text printed in SVG/PDF layout metadata.")
    common_render.add_argument("--contour-interval-m", default="auto", help="Contour interval label for SVG/PDF layout metadata, or auto.")
    common_render.add_argument(
        "--contour-merge-tolerance-m",
        type=float,
        default=DEFAULT_CONTOUR_MERGE_TOLERANCE_M,
        help="Join same-elevation contour fragments with endpoints this many meters apart before rendering.",
    )
    common_render.add_argument("--north-line-spacing-m", type=float, default=DEFAULT_NORTH_LINE_SPACING_M)
    common_render.add_argument("--no-layout", action="store_true", help="Render only map geometry, without title, footer, frame, or north lines.")
    common_render.add_argument(
        "--magnetic-declination-deg",
        default="auto",
        help="Magnetic north east of EPSG:3067/grid north in degrees, or auto for estimated KOK. Rotates the rendered map frame.",
    )
    common_render.add_argument("--magnetic-date", help="Date for automatic magnetic declination as YYYY-MM-DD.")

    render_parser = subparsers.add_parser(
        "render",
        parents=[common_render],
        help="Render GeoJSON to PNG, PDF, and a symbol-number PDF.",
    )
    render_parser.add_argument("--dpi", type=int, default=300)
    render_parser.set_defaults(func=command_render)

    render_svg_parser = subparsers.add_parser("render-svg", parents=[common_render], help="Render GeoJSON to SVG.")
    render_svg_parser.set_defaults(func=command_render_svg)

    render_png_parser = subparsers.add_parser("render-png", parents=[common_render], help="Render GeoJSON to PNG.")
    render_png_parser.add_argument("--dpi", type=int, default=300)
    render_png_parser.set_defaults(func=command_render_png)

    render_pdf_parser = subparsers.add_parser("render-pdf", parents=[common_render], help="Render GeoJSON to PDF.")
    render_pdf_parser.add_argument("--symbol-numbers", action="store_true", help="Print IOF/ISOM symbol numbers over rendered features.")
    render_pdf_parser.set_defaults(func=command_render_pdf)

    symbols = subparsers.add_parser("symbols", help="Write the built-in structured ISOM symbol library as JSON.")
    symbols.add_argument("output", nargs="?", help="Optional JSON output path. Defaults to stdout.")
    symbols.set_defaults(func=command_symbols)

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
