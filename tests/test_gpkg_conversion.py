import sqlite3
import struct
import tempfile
import unittest
import argparse
import datetime as dt
from pathlib import Path

import mml_omap.cli as cli
from mml_omap.symbols import export_symbol_library
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
    feature_symbol,
    meridian_convergence_deg,
    geojson_bbox,
    geojson_map_frame_declination,
    infer_contour_interval_m,
    iof_symbol_metadata,
    merge_contour_features,
    read_env_file_value,
    render_output_base,
    render_pdf,
    render_svg,
    command_symbols,
    contour_features_from_xyz_grid,
    command_contours_from_xyz,
    cliff_features_from_xyz_grid,
    lidar_vegetation_features,
    merge_geojson_documents,
    should_render_point_symbol,
    table_rules_with_optional_forest_mask,
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
        self.assertEqual(feature["properties"]["symbol"], "506")
        self.assertEqual(feature["properties"]["object_type"], "line")
        self.assertEqual(feature["properties"]["iof_symbol_number"], "506")
        self.assertEqual(feature["properties"]["iof_symbol_name"], "Small footpath")

    def test_convert_gpkg_does_not_emit_non_isom_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            gpkg = Path(directory) / "sample.gpkg"
            connection = sqlite3.connect(gpkg)
            connection.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT)")
            connection.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT)")
            connection.execute("INSERT INTO gpkg_contents VALUES ('paikannimi', 'features')")
            connection.execute("INSERT INTO gpkg_geometry_columns VALUES ('paikannimi', 'geom')")
            connection.execute("CREATE TABLE paikannimi (id INTEGER PRIMARY KEY, kohdeluokka INTEGER, teksti TEXT, geom BLOB)")
            wkb = b"\x01" + struct.pack("<I", 1) + struct.pack("<dd", 385396.0, 6672568.0)
            gpkg_geometry = b"GP" + bytes([0, 1]) + struct.pack("<I", 3067) + wkb
            connection.execute(
                "INSERT INTO paikannimi (kohdeluokka, teksti, geom) VALUES (?, ?, ?)",
                (35010, "Lillträsk", gpkg_geometry),
            )
            connection.commit()
            connection.close()

            geojson = convert_gpkg_to_geojson(
                gpkg,
                bbox=[385395, 6672567, 385397, 6672569],
                table_rules=DEFAULT_TABLE_RULES,
                include_unmapped=False,
            )

        self.assertEqual(geojson["features"], [])

    def test_optional_forest_mask_maps_mml_forest_to_green_proxy(self) -> None:
        rules = table_rules_with_optional_forest_mask(DEFAULT_TABLE_RULES, include_forest_mask=True)

        object_type, symbol = cli.classify_feature(
            "metsamaankasvillisuus",
            {},
            {"type": "Polygon", "coordinates": []},
            rules["metsamaankasvillisuus"],
        )

        self.assertEqual(object_type, "area")
        self.assertEqual(symbol, "406")
        self.assertEqual(iof_symbol_metadata(symbol)["iof_symbol_name"], "Vegetation: slow running")

    def test_forest_mask_is_not_enabled_by_default(self) -> None:
        rules = table_rules_with_optional_forest_mask(DEFAULT_TABLE_RULES, include_forest_mask=False)

        self.assertNotIn("metsamaankasvillisuus", rules)

    def test_area_symbol_points_are_not_rendered_as_green_dots(self) -> None:
        feature = {
            "type": "Feature",
            "properties": {"symbol": "406", "iof_symbol_number": "406", "object_type": "area"},
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        }

        self.assertFalse(should_render_point_symbol(feature))

    def test_real_point_symbols_still_render_as_points(self) -> None:
        feature = {
            "type": "Feature",
            "properties": {"symbol": "204", "iof_symbol_number": "204", "object_type": "point"},
            "geometry": {"type": "Point", "coordinates": [0, 0]},
        }

        self.assertTrue(should_render_point_symbol(feature))


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

    def test_tieviiva_symbol_is_refined_from_kohdeluokka_at_render_time(self) -> None:
        self.assertEqual(
            feature_symbol({"properties": {"source_table": "tieviiva", "symbol": "502", "iof_symbol_number": "502"}}),
            "major_road",
        )
        self.assertEqual(
            feature_symbol({"properties": {"source_table": "tieviiva", "symbol": "path", "kohdeluokka": 12314}}),
            "road",
        )
        self.assertEqual(
            feature_symbol({"properties": {"source_table": "virtavesikapea", "symbol": "stream", "kohdeluokka": 36312}}),
            "wide_stream",
        )

    def test_iof_symbol_metadata_is_available_for_known_symbols(self) -> None:
        self.assertEqual(iof_symbol_metadata("lake")["iof_symbol_number"], "301")
        self.assertEqual(iof_symbol_metadata("swamp")["iof_symbol_number"], "308")
        self.assertEqual(iof_symbol_metadata("major_road")["iof_symbol_number"], "502")
        self.assertIsNone(iof_symbol_metadata("place_label")["iof_symbol_number"])

    def test_symbol_library_exports_structured_local_definitions(self) -> None:
        library = export_symbol_library()

        self.assertEqual(library["source"]["standard"], "ISOM 2017-2 Revision 6")
        self.assertIn("Do not import GPL/proprietary", library["source"]["asset_policy"])
        self.assertEqual(library["symbols"]["major_road"]["iof_symbol_number"], "502")
        self.assertEqual(library["symbols"]["major_road"]["geometry"], "line")
        self.assertEqual(library["symbols"]["major_road"]["style"]["inner_stroke"], "#b68a57")

    def test_svg_render_uses_iof_like_water_marsh_field_and_road_symbols(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"symbol": "lake", "object_type": "area"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [300, 0], [300, 300], [0, 300], [0, 0]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"source_table": "suo", "symbol": "swamp", "object_type": "area"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[350, 0], [650, 0], [650, 300], [350, 300], [350, 0]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"source_table": "maatalousmaa", "symbol": "field", "object_type": "area"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[700, 0], [1000, 0], [1000, 300], [700, 300], [700, 0]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"source_table": "tieviiva", "symbol": "road", "kohdeluokka": 12121},
                    "geometry": {"type": "LineString", "coordinates": [[0, 500], [1000, 500]]},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.svg"
            render_svg(geojson, output, transform=RenderTransform([0, 0, 1000, 600], 5000, 5))
            svg = output.read_text(encoding="utf-8")

        self.assertIn('stroke="#000000" fill="#b9e3f7"', svg)
        self.assertIn('clip-path="url(#marsh-clip-', svg)
        self.assertIn('stroke="#008fd5"', svg)
        self.assertIn('stroke-width="0.120"', svg)
        self.assertIn('stroke-dasharray="1.4 0.55"', svg)
        self.assertNotIn('fill="url(#marsh)"', svg)
        self.assertIn("url(#cultivated-land)", svg)
        self.assertIn('stroke="#b68a57"', svg)

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
        self.assertIn("Contours 5 m", svg)
        self.assertIn("Test maker", svg)
        self.assertIn("KOK 8.50 deg", svg)
        self.assertIn('stroke="#6f2dbd"', svg)

    def test_svg_render_draws_place_and_water_labels(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"symbol": "water_label", "object_type": "point", "teksti": "Lillträsk"},
                    "geometry": {"type": "Point", "coordinates": [500, 250]},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.svg"
            render_svg(geojson, output, transform=RenderTransform([0, 0, 1000, 500], 5000, 5))
            svg = output.read_text(encoding="utf-8")

        self.assertIn(">Lillträsk</text>", svg)
        self.assertIn('fill="#008fd5"', svg)
        self.assertNotIn('<circle cx="105.000"', svg)

    def test_pdf_render_uses_winansi_for_finnish_and_swedish_letters(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"symbol": "water_label", "object_type": "point", "teksti": "Mössenkärr"},
                    "geometry": {"type": "Point", "coordinates": [500, 250]},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.pdf"
            render_pdf(
                geojson,
                output,
                transform=RenderTransform([0, 0, 1000, 500], 5000, 5),
                map_title_text="Mössenkärr",
            )
            pdf = output.read_bytes()

        self.assertIn(b"/Encoding /WinAnsiEncoding", pdf)
        self.assertIn("Mössenkärr".encode("latin-1"), pdf)

    def test_pdf_render_can_print_symbol_numbers(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"symbol": "308", "iof_symbol_number": "308", "object_type": "area"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]],
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.pdf"
            render_pdf(
                geojson,
                output,
                transform=RenderTransform([0, 0, 100, 100], 5000, 5),
                include_symbol_numbers=True,
            )
            pdf = output.read_bytes()

        self.assertIn(b"(308) Tj", pdf)

    def test_render_output_base_accepts_suffix_or_plain_base(self) -> None:
        self.assertEqual(render_output_base("map"), Path("map"))
        self.assertEqual(render_output_base("map.pdf"), Path("map"))
        self.assertEqual(render_output_base("map.png"), Path("map"))

    def test_contour_fragments_with_same_height_are_merged_for_rendering(self) -> None:
        features = [
            {
                "type": "Feature",
                "properties": {"symbol": "contour", "korkeusarvo": 25000},
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [10, 0]]},
            },
            {
                "type": "Feature",
                "properties": {"symbol": "contour", "korkeusarvo": 25000},
                "geometry": {"type": "LineString", "coordinates": [[10.5, 0], [20, 0]]},
            },
        ]

        merged = merge_contour_features(features, tolerance_m=1.0)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["geometry"]["type"], "LineString")
        self.assertEqual(merged[0]["geometry"]["coordinates"], [[0, 0], [10, 0], [20, 0]])

    def test_contour_interval_is_inferred_from_mml_height_values(self) -> None:
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"symbol": "contour", "korkeusarvo": 15000}, "geometry": None},
                {"type": "Feature", "properties": {"symbol": "contour", "korkeusarvo": 17500}, "geometry": None},
                {"type": "Feature", "properties": {"symbol": "contour", "korkeusarvo": 20000}, "geometry": None},
            ],
        }

        self.assertEqual(infer_contour_interval_m(geojson), 2.5)

    def test_lidar_xyz_grid_generates_contour_lines(self) -> None:
        xs = [0.0, 10.0]
        ys = [0.0, 10.0]
        points = {
            (0.0, 0.0): 0.0,
            (10.0, 0.0): 10.0,
            (0.0, 10.0): 0.0,
            (10.0, 10.0): 10.0,
        }

        features = contour_features_from_xyz_grid(xs, ys, points, interval_m=5.0)

        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["symbol"], "101")
        self.assertEqual(features[0]["properties"]["korkeusarvo"], 5000)
        self.assertEqual(features[0]["geometry"]["type"], "LineString")

    def test_lidar_xyz_grid_marks_index_contours(self) -> None:
        xs = [0.0, 10.0]
        ys = [0.0, 10.0]
        points = {
            (0.0, 0.0): 0.0,
            (10.0, 0.0): 30.0,
            (0.0, 10.0): 0.0,
            (10.0, 10.0): 30.0,
        }

        features = contour_features_from_xyz_grid(xs, ys, points, interval_m=5.0, index_contour_every=2)

        self.assertTrue(any(feature["properties"]["symbol"] == "102" for feature in features))

    def test_lidar_xyz_grid_generates_candidate_cliffs(self) -> None:
        xs = [0.0, 10.0, 20.0]
        ys = [0.0, 10.0, 20.0]
        points = {
            (x, y): (20.0 if x >= 10.0 else 0.0)
            for x in xs
            for y in ys
        }

        features = cliff_features_from_xyz_grid(xs, ys, points, slope_threshold_deg=30.0, min_length_m=1.0)

        self.assertTrue(features)
        self.assertEqual(features[0]["properties"]["symbol"], "202")

    def test_lidar_points_generate_dissolved_vegetation_features(self) -> None:
        rows = [
            (0.0, 0.0, 0.0, 2),
            (1.0, 1.0, 3.0, 5),
            (2.0, 2.0, 3.2, 5),
            (3.0, 3.0, 3.4, 5),
            (4.0, 4.0, 3.6, 5),
        ]

        features = lidar_vegetation_features(
            rows,
            cell_size_m=10.0,
            min_height_m=1.8,
            slow_count=2,
            fight_count=10,
        )

        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["symbol"], "406")
        self.assertEqual(features[0]["geometry"]["type"], "Polygon")

    def test_contours_from_xyz_command_writes_geojson(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "dem.xyz"
            output_path = Path(directory) / "contours.geojson"
            input_path.write_text("0 0 0\n10 0 10\n0 10 0\n10 10 10\n", encoding="utf-8")

            exit_code = command_contours_from_xyz(
                argparse.Namespace(
                    input=str(input_path),
                    output=str(output_path),
                    interval_m=5.0,
                    min_level=None,
                    max_level=None,
                    bbox=None,
                    magnetic_declination_deg="auto",
                    magnetic_date=None,
                )
            )

            self.assertEqual(exit_code, 0)
            geojson = cli.read_json(output_path)
            self.assertEqual(len(geojson["features"]), 1)
            self.assertEqual(geojson["features"][0]["properties"]["iof_symbol_number"], "101")


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

        self.assertEqual(Path(download_args.output), Path("builds/output.zip"))
        self.assertEqual(download_args.bbox, "0,0,1,1")

    def test_symbols_command_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "symbols.json"

            exit_code = command_symbols(argparse.Namespace(output=str(output)))

            self.assertEqual(exit_code, 0)
            data = cli.read_json(output)
            self.assertEqual(data["symbols"]["lake"]["iof_symbol_number"], "301")

    def test_merge_geojson_documents_combines_features_and_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.geojson"
            second = Path(directory) / "second.geojson"
            cli.write_json(
                first,
                {
                    "type": "FeatureCollection",
                    "map_frame": {"bbox": [0, 0, 10, 10], "magnetic_declination_deg": 0.0},
                    "features": [{"type": "Feature", "properties": {"symbol": "101"}, "geometry": None}],
                },
            )
            cli.write_json(
                second,
                {
                    "type": "FeatureCollection",
                    "features": [{"type": "Feature", "properties": {"symbol": "202"}, "geometry": None}],
                },
            )

            merged = merge_geojson_documents([first, second])

        self.assertEqual(len(merged["features"]), 2)
        self.assertEqual(merged["map_frame"]["bbox"], [0, 0, 10, 10])


if __name__ == "__main__":
    unittest.main()
