# mml-geojson

Download Maanmittauslaitos open topographic data by bounding box and convert it
to GeoJSON.

The tool is intentionally small:

- Uses MML's Paikkatiedon tiedostopalvelu OGC API Processes endpoint.
- Submits `maastotietokanta_bbox` jobs.
- Downloads the returned GeoPackage package.
- Converts selected GeoPackage feature tables to GeoJSON.
- Adds optional `symbol` and `object_type` properties for downstream map tools.
- Uses only the Python standard library.

Coordinates are ETRS-TM35FIN / EPSG:3067 meters, matching the native MML file
service output.

## Existing Tools

There are generic tools that cover parts of this workflow:

- `ogc-api-processes-client` is a generic OGC API Processes Python client.
- GDAL/OGR can convert GeoPackage to GeoJSON once you already have the data.
- Other GeoPackage/GeoJSON utilities can inspect or convert local files.

This project exists to combine the MML-specific job request, API-key auth,
download handling, GeoPackage geometry decoding, and practical table-to-symbol
mapping into one CLI.

## Install

From a local checkout:

```sh
python3 -m pip install .
```

Or run without installing:

```sh
python3 -m mml_geojson.cli --help
```

## API Key

MML open APIs require an API key. Create one in Maanmittauslaitos OmaTili and
set it in your environment:

```sh
export MML_API_KEY="your-api-key"
```

You can also pass `--api-key`, but the environment variable is better for shell
history.

## Generate GeoJSON From A BBOX

Bounding boxes are `min_x,min_y,max_x,max_y` in EPSG:3067 meters:

```sh
mml-geojson generate output.geojson \
  --bbox 385396,6672568,389620,6677160
```

By default, temporary downloads are kept under `builds/mml_downloads`. Override
with `--work-dir`.

## Download Only

```sh
mml-geojson download mml_area.zip \
  --bbox 385396,6672568,389620,6677160
```

## Convert An Existing GeoPackage

```sh
mml-geojson convert-gpkg maastotietokanta.gpkg output.geojson \
  --bbox 385396,6672568,389620,6677160
```

## Mapping

The default mapping is conservative:

- `tieviiva` -> `road` or `path` by `kohdeluokka`
- `korkeuskayra` -> `contour`
- `jyrkanne` -> `cliff`
- `virtavesikapea` -> `stream`
- `jarvi`, `meri` -> `lake`
- `virtavesialue` -> `river`
- `suo`, `soistuma` -> `swamp`
- `maatalousmaa`, `niitty`, `muuavoinalue`, `puisto`, `urheilujavirkistysalue` -> `field`
- `kallioalue` -> `open_rock`
- `rakennus` -> `building`
- `kivi` -> `mapped_rock`
- `aita` -> `fence`

Override or extend mappings with JSON:

```json
{
  "metsamaankasvillisuus": {
    "symbol": "thick_forest",
    "object_type": "area"
  },
  "tieviiva": {
    "object_type": "line",
    "symbol": "path",
    "kohdeluokka": {
      "12111": "road",
      "12316": "path"
    }
  }
}
```

Use it with:

```sh
mml-geojson convert-gpkg maastotietokanta.gpkg output.geojson \
  --mapping mml_mapping.json
```

## Notes

The output is public interchange data, not an OCAD or OpenOrienteering Mapper
project. Use GeoJSON-capable GIS tools or a downstream map generator to render
or convert it further.

Maanmittauslaitos data licensing and attribution requirements still apply to
the downloaded data.
