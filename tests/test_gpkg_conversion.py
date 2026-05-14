import sqlite3
import struct
import tempfile
import unittest
import argparse
import datetime as dt
from pathlib import Path

import mml_omap.cli as cli
from mml_omap.cli import (
    DEFAULT_TABLE_RULES,
    OrientedFrame,
    RenderTransform,
    clip_geometry_to_frame,
    convert_gpkg_to_geojson,
    download_args_for_generate,
    enclosing_grid_bbox,
    estimate_finland_magnetic_declination_deg,
    estimate_finland_total_correction_deg,
    meridian_convergence_deg,
    geojson_bbox,
    geojson_map_frame_declination,
    read_env_file_value,
    render_svg,
    validate_orienteering_bbox_size,
)


def setUpModule() -> None:
    cli.PROGRESS_ENABLED = False


def make_gpkg(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
    connection.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT)")
    connection.execute("INSERT INTO gpkg_contents VALUES ('tieviiva', 'features')")
    connection.execute("INSERT INTO gpkg_geometry_columns VALUES ('tieviiva', 'geom')")
    connection.execute("CREATE TABLE tieviiva (id INTEGER PRIMARY KEY, kohdeluokka INTEGER, geom BLOB)")
    wkb = (
        b"\x01"
        + struct.pack("<I", 2)
        + struct.pack("<I", 2)
        + struct.pack("<dddd", 385396.0, 6672568.0, 385400.0, 6672572.0)
    )
    gpkg = b"GP" + bytes([0, 1]) + struct.pack("<I", 3067) + wkb
    connection.execute("INSERT INTO tieviiva (kohdeluokka, geom) VALUES (?, ?)", (12312, gpkg))
    connection.commit()
    connection.close()


class GeoPackageConversionTest(unittest.TestCase):
    def test_convert_gpkg_to_geojson(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gpkg = Path(directory) / "sample.gpkg"
            make_gpkg(gpkg)

            geojson = convert_gpkg_to_geojson(
                gpkg,
                bbox=[385395, 6672567, 385401, 6672573],
                table_rules=DEFAULT_TABLE_RULES,
                include_unmapped=False,
            )

        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertEqual(len(geojson["features"]), 1)
        feature = geojson["features"][0]
        self.assertEqual(feature["geometry"]["type"], "LineString")
        self.assertEqual(feature["properties"]["source_table"], "tieviiva")
        self.assertEqual(feature["properties"]["symbol"], "path")
        self.assertEqual(feature["properties"]["object_type"], "line")


class OrienteeringBoundsTest(unittest.TestCase):
    def test_a3_15000_bbox_limit_accepts_portrait_or_landscape(self) -> None:
        validate_orienteering_bbox_size([0, 0, 6300, 4455])
        validate_orienteering_bbox_size([0, 0, 4455, 6300])

    def test_a3_15000_bbox_limit_rejects_oversized_rectangle(self) -> None:
        with self.assertRaisesRegex(ValueError, "too large"):
            validate_orienteering_bbox_size([0, 0, 6301, 4455])

    def test_magnetic_declination_expands_mml_fetch_bbox(self) -> None:
        bbox = [0, 0, 1000, 2000]
        expanded = enclosing_grid_bbox(bbox, 10.0)

        self.assertLess(expanded[0], bbox[0])
        self.assertLess(expanded[1], bbox[1])
        self.assertGreater(expanded[2], bbox[2])
        self.assertGreater(expanded[3], bbox[3])

    def test_render_transform_places_magnetic_north_up(self) -> None:
        transform = RenderTransform([0, 0, 1000, 1000], scale=10000, margin_mm=0, magnetic_declination_deg=10.0)
        center = [500.0, 500.0]
        magnetic_north = [500.0 + 100.0 * 0.1736481777, 500.0 + 100.0 * 0.9848077530]

        center_x, center_y = transform.to_mm(center)
        north_x, north_y = transform.to_mm(magnetic_north)

        self.assertAlmostEqual(center_x, north_x, places=6)
        self.assertLess(north_y, center_y)

    def test_automatic_declination_estimate_is_plausible_for_finland(self) -> None:
        declination = estimate_finland_magnetic_declination_deg(385396, 6672568, dt.date(2026, 1, 1))

        self.assertAlmostEqual(declination, 10.35, places=2)

    def test_total_correction_includes_grid_convergence(self) -> None:
        date = dt.date(2026, 5, 13)
        declination = estimate_finland_magnetic_declination_deg(385396, 6672568, date)
        correction = estimate_finland_total_correction_deg(385396, 6672568, date)
        convergence = meridian_convergence_deg(60.17387413161526, 24.93426897144729)

        self.assertLess(convergence, 0.0)
        self.assertAlmostEqual(correction, declination + convergence, places=6)

    def test_line_is_clipped_to_rotated_paper_frame(self) -> None:
        frame = OrientedFrame([0, 0, 1000, 1000], 10.0)
        geometry = {"type": "LineString", "coordinates": [[-500, 500], [1500, 500]]}

        clipped = clip_geometry_to_frame(geometry, frame)

        self.assertIsNotNone(clipped)
        self.assertEqual(clipped["type"], "LineString")
        for coordinate in clipped["coordinates"]:
            self.assertTrue(frame.contains_local(frame.to_local(coordinate)))

    def test_geojson_map_frame_metadata_is_used_for_render_bounds(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "map_frame": {"bbox": [0, 0, 1000, 2000], "magnetic_declination_deg": 10.0},
            "features": [],
        }

        self.assertEqual(geojson_bbox(geojson), [0.0, 0.0, 1000.0, 2000.0])
        self.assertEqual(geojson_map_frame_declination(geojson), 10.0)

    def test_svg_render_draws_area_fills_below_contours(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"symbol": "contour", "object_type": "line"},
                    "geometry": {"type": "LineString", "coordinates": [[0, 500], [1000, 500]]},
                },
                {
                    "type": "Feature",
                    "properties": {"symbol": "field", "object_type": "area"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [1000, 0], [1000, 1000], [0, 1000], [0, 0]]],
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.svg"
            render_svg(geojson, output, transform=RenderTransform([0, 0, 1000, 1000], 10000, 0))
            svg = output.read_text(encoding="utf-8")

        self.assertLess(svg.index('fill="#f2c84b"'), svg.index('stroke="#9b5a28"'))

    def test_svg_render_includes_layout_metadata_and_north_lines(self) -> None:
        geojson = {"type": "FeatureCollection", "features": []}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.svg"
            render_svg(
                geojson,
                output,
                transform=RenderTransform([0, 0, 1000, 500], 5000, 5, magnetic_declination_deg=8.5),
                map_title_text="Test map",
                map_maker="Test maker",
            )
            svg = output.read_text(encoding="utf-8")

        self.assertIn("Test map", svg)
        self.assertIn("Scale 1:5000", svg)
        self.assertIn("Test maker", svg)
        self.assertIn("KOK 8.50 deg", svg)
        self.assertIn('stroke="#6f2dbd"', svg)


class EnvFileTest(unittest.TestCase):
    def test_read_env_file_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("# comment\nMML_API_KEY='secret-value'\nOTHER=x\n", encoding="utf-8")

            self.assertEqual(read_env_file_value("MML_API_KEY", env_path), "secret-value")
            self.assertIsNone(read_env_file_value("MISSING", env_path))


class GenerateCommandTest(unittest.TestCase):
    def test_download_args_for_generate_replaces_output(self) -> None:
        args = argparse.Namespace(output="output.geojson", bbox="0,0,1,1", work_dir="builds")
        download_args = download_args_for_generate(args, Path("builds/output.zip"))

        self.assertEqual(download_args.output, "builds/output.zip")
        self.assertEqual(download_args.bbox, "0,0,1,1")


if __name__ == "__main__":
    unittest.main()
