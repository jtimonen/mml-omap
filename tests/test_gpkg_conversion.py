import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from mml_geojson.cli import DEFAULT_TABLE_RULES, convert_gpkg_to_geojson


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


if __name__ == "__main__":
    unittest.main()
