import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from mml_omap.cli import (
    DEFAULT_TABLE_RULES,
    RenderTransform,
    convert_gpkg_to_geojson,
    enclosing_grid_bbox,
    validate_orienteering_bbox_size,
)


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


if __name__ == "__main__":
    unittest.main()
