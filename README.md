# mml-omap

Generate orienteering-oriented map data and map previews from
Maanmittauslaitos open topographic data. The goal is to make actual
orienteering maps, plus useful intermediate GeoJSON files, from MML data alone.

This is still alpha software. It can already download MML data, convert selected
features to symbolized GeoJSON, and render simple SVG/PNG/PDF previews. It is
not yet a complete ISOM/ISSprOM-quality cartographic production tool.

The tool is intentionally small:

- Uses MML's Paikkatiedon tiedostopalvelu OGC API Processes endpoint.
- Submits `maastotietokanta_bbox` jobs.
- Downloads the returned GeoPackage package.
- Converts selected GeoPackage feature tables to GeoJSON.
- Adds optional `symbol` and `object_type` properties.
- Renders GeoJSON to SVG, PNG, or PDF.
- Supports magnetic-north-oriented paper frames for rendered maps.
- Uses only the Python standard library.

Coordinates are ETRS-TM35FIN / EPSG:3067 meters, matching the native MML file
service output.

## Data Sources And Coverage

Today, `mml-omap` uses only the GeoPackage returned by MML's
`maastotietokanta_bbox` process from Paikkatiedon tiedostopalvelu. It does not
currently download or process MML laser scanning point clouds, elevation models,
hillshade rasters, aerial imagery, forest inventory rasters, or other raster
products.

That means the current output is useful as a generated base map and as
intermediate GeoJSON, but it is not enough by itself for a finished,
field-checked orienteering map. Real orienteering maps usually need better
contour generation, vegetation runnability interpretation, generalization,
symbol conflict handling, and human cartographic review.

Current source usage by feature type:

- Contours: read from the MML topographic database GeoPackage table
  `korkeuskayra` and rendered as `contour`. The tool does not currently derive
  contours directly from laser scanning data or an elevation model.
- Vegetation: not meaningfully generated yet. Some open or semi-open land-cover
  tables such as `maatalousmaa`, `niitty`, `muuavoinalue`, `puisto`, and
  `urheilujavirkistysalue` are mapped as `field`, and `kallioalue` is mapped as
  `open_rock`. Forest vegetation and runnability need future source mapping or
  raster analysis.
- Lakes and bodies of water: read from `jarvi` and `meri`, mapped as `lake`.
- Streams and rivers: narrow streams are read from `virtavesikapea` and mapped
  as `stream`; wider water areas are read from `virtavesialue` and mapped as
  `river`.
- Swamps: read from `suo` and `soistuma`, mapped as `swamp`.
- Cliffs: read from `jyrkanne`, mapped as `cliff`. The tool does not currently
  infer cliffs from slope, laser scanning, or elevation models.
- Roads: read from `tieviiva`. Selected `kohdeluokka` values are mapped as
  `road`.
- Paths: read from `tieviiva`. Selected `kohdeluokka` values are mapped as
  `path`.
- Forest density: not currently generated. No laser-scan, canopy, forest
  inventory, or vegetation-density raster is processed.
- Buildings and other human-built objects: `rakennus` is mapped as `building`,
  `rakennusreunaviiva` as building linework, and `aita` as `fence`. Other
  human-made features require more table mappings.
- Rocks: `kivi` is mapped as `mapped_rock`. The tool does not currently infer
  boulders or rocky ground from laser scanning or imagery.

## Orientation And Size

MML's `boundingBoxInput` is an axis-aligned rectangle in EPSG:3067 coordinates.
That is not the same thing as the rectangle printed on an orienteering map when
the map is oriented to magnetic north.

In `mml-omap`, `--bbox` is treated as the intended paper map frame. If you pass
`--magnetic-declination-deg`, the tool expands the MML download request to the
smallest EPSG:3067 bbox that contains that rotated paper frame, then renders the
paper frame with magnetic north pointing up.

The requested paper rectangle is limited to A3 at 1:15000, or 6300 m x 4455 m
in either portrait or landscape orientation. Larger rectangles error before a
download job is submitted.

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
PYTHONPATH=src python3 -m mml_omap.cli --help
```

For development, use an isolated environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
python3 -m unittest discover -s tests
```

## API Key Setup

MML open API services require an API key for Paikkatiedon tiedostopalvelu
(OGC API Processes), which this tool uses. Create the key in Maanmittauslaitos
OmaTili using MML's API key instructions:

- API key instructions: https://www.maanmittauslaitos.fi/rajapinnat/api-avaimen-ohje
- OGC API Processes service: https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1/

After creating the key, set it in your shell:

```sh
export MML_API_KEY="your-api-key"
```

You can also pass `--api-key`, but the environment variable is better for shell
history.

If you prefer a local `.env` file for your own shell tooling, do not commit it:

```sh
MML_API_KEY=your-api-key
```

## Generate GeoJSON From A BBOX

Bounding boxes are `min_x,min_y,max_x,max_y` in EPSG:3067 meters:

```sh
mml-omap generate output.geojson \
  --bbox 385396,6672568,389620,6677160
```

For a magnetic-north-oriented paper frame, pass local declination in degrees.
Positive values mean magnetic north is east of EPSG:3067/grid north:

```sh
mml-omap generate output.geojson \
  --bbox 385396,6672568,389620,6677160 \
  --magnetic-declination-deg 10.5
```

By default, temporary downloads are kept under `builds/mml_downloads`. Override
with `--work-dir`.

## Download Only

```sh
mml-omap download mml_area.zip \
  --bbox 385396,6672568,389620,6677160
```

With `--magnetic-declination-deg`, this command downloads the enclosing MML bbox
for the rotated paper frame.

## Convert An Existing GeoPackage

```sh
mml-omap convert-gpkg maastotietokanta.gpkg output.geojson \
  --bbox 385396,6672568,389620,6677160
```

## Render GeoJSON

Render SVG:

```sh
mml-omap render-svg output.geojson map.svg
```

Render PNG:

```sh
mml-omap render-png output.geojson map.png --dpi 300
```

Render PDF:

```sh
mml-omap render-pdf output.geojson map.pdf
```

Rendering uses the GeoJSON extent by default. Pass `--bbox` to force the map
frame:

```sh
mml-omap render-pdf output.geojson map.pdf \
  --bbox 385396,6672568,389620,6677160 \
  --magnetic-declination-deg 10.5 \
  --scale 10000 \
  --margin-mm 5
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
mml-omap convert-gpkg maastotietokanta.gpkg output.geojson \
  --mapping mml_mapping.json
```

## Notes

The GeoJSON output is public interchange data, not a proprietary map project.
Use GeoJSON-capable GIS tools or the built-in render commands to inspect it.

Maanmittauslaitos data licensing and attribution requirements still apply to
the downloaded data.

Generated data and map files can be large. The repository `.gitignore` excludes
download archives, GeoPackages, rendered maps, local build folders, caches,
virtual environments, and `.env` files. Keep curated fixtures or examples in a
dedicated tracked directory and unignore them explicitly if needed.

## Future Development Plans

Near-term work toward real MML-only orienteering map generation:

- Add automatic magnetic declination lookup or calculation from map center and
  date, instead of requiring a manual `--magnetic-declination-deg` value.
- Clip geometry to the rotated paper frame, not only to the enclosing MML bbox.
- Add optional MML elevation model or laser scanning ingestion for direct,
  configurable contour generation.
- Investigate source data for vegetation and forest density, including whether
  MML topographic classes, forest inventory data, laser scanning, or derived
  rasters can produce useful runnability estimates.
- Expand MML table mappings into a fuller ISOM/ISSprOM-oriented symbol model.
- Add contour handling that is suitable for orienteering, including better
  index contour and form-line support where source data allows it.
- Improve vegetation, marsh, rock, water, road, path, and building
  generalization from MML source classes.
- Add layout elements expected on real printed maps: north lines, scale,
  attribution, title, legend options, and print margins.
- Add higher-fidelity SVG/PDF rendering with proper overprint order, line
  joins, masks, and symbol dimensions.
- Add regression fixtures from small public MML extracts so map output changes
  can be reviewed safely.
