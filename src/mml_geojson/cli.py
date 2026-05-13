"""Command line interface for exporting MML open data to GeoJSON.

The primary output is GeoJSON in EPSG:3067 coordinates. When the default mapping
is enabled, features also get `symbol` and `object_type` properties that make the
output convenient for downstream map generators.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


MML_OGC_PROCESSES_URL = (
    "https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1"
)

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
    return {
        "type": "FeatureCollection",
        "name": "mml-geojson",
        "crs": {"type": "name", "properties": {"name": "EPSG:3067"}},
        "features": features,
    }


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


def collect_xy(raw: Any, xs: list[float], ys: list[float]) -> None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2 and all(isinstance(v, (int, float)) for v in raw[:2]):
        xs.append(float(raw[0]))
        ys.append(float(raw[1]))
        return
    if isinstance(raw, (list, tuple)):
        for item in raw:
            collect_xy(item, xs, ys)


def command_download(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get(args.api_key_env)
    if not api_key:
        raise ValueError(f"Provide --api-key or set ${args.api_key_env}")
    bbox = parse_bbox(args.bbox)
    job_url = submit_mml_bbox_job(
        api_key=api_key,
        bbox=bbox,
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
    bbox = parse_bbox(args.bbox) if args.bbox else None
    rules = load_table_rules(Path(args.mapping) if args.mapping else None)
    geojson = convert_gpkg_to_geojson(
        Path(args.input),
        bbox=bbox,
        table_rules=rules,
        include_unmapped=args.include_unmapped,
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
    geojson = convert_gpkg_to_geojson(
        gpkg_path,
        bbox=parse_bbox(args.bbox),
        table_rules=rules,
        include_unmapped=args.include_unmapped,
    )
    write_json(output, geojson)
    print(f"Wrote {len(geojson['features'])} features from {gpkg_path}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create symbolized GeoJSON from Maanmittauslaitos open data."
    )
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
    download.add_argument("--theme", default="maastotietokanta_kaikki")
    download.set_defaults(func=command_download)

    convert = subparsers.add_parser("convert-gpkg", help="Convert an existing MML GeoPackage to symbolized GeoJSON.")
    convert.add_argument("input")
    convert.add_argument("output")
    convert.add_argument("--bbox", help="Optional min_x,min_y,max_x,max_y clip in EPSG:3067 meters")
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
    generate.add_argument("--theme", default="maastotietokanta_kaikki")
    generate.add_argument("--work-dir", default="builds/mml_downloads")
    generate.add_argument("--mapping", help="JSON table mapping overrides")
    generate.add_argument("--include-unmapped", action="store_true")
    generate.set_defaults(func=command_generate)

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
