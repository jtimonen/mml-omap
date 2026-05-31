# mml-omap

![Espoon keskuspuisto demo map](docs/ekp.png)

Generate orienteering-oriented maps from Maanmittauslaitos vector data and
laser scanning point clouds.

The normal workflow is one command: download MML topographic vector data,
download the covering 0.5 p LAZ map sheets, build the terrain model from the
point cloud, then write GeoJSON, PNG, PDF, LiDAR raster PNGs, and a terrain
report:

```sh
uv run mml-omap create 60.188068,24.696799 --name my-place
```

Rendered maps use the smallest A5, A4, or A3 portrait/landscape page that fits
the requested map frame at the requested scale.

The `create` command accepts WGS84 latitude,longitude degrees and converts them
internally. Lower-level commands use ETRS-TM35FIN / EPSG:3067 meters, matching
the native MML file service output.

## Install

From a local checkout:

```sh
uv sync
uv run mml-omap --help
```

Run tests:

```sh
uv run python -m unittest discover -s tests
```

## API Key

The combined build downloads data from MML Paikkatiedon tiedostopalvelu OGC API
Processes, so it needs an MML API key.

Create a key with MML's instructions:

- https://www.maanmittauslaitos.fi/rajapinnat/api-avaimen-ohje
- https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1/

Set the key in the shell:

```sh
export MML_API_KEY="your-api-key"
```

Or put it in a local `.env` file in the repository root:

```sh
MML_API_KEY=your-api-key
```

Do not commit `.env`.

## Inputs

`mml-omap build` combines data sources for the same EPSG:3067 map frame:

- MML vector data downloaded automatically from `maastotietokanta_bbox`.
- MML 0.5 p laser scanning data downloaded automatically from
  `laserkeilausaineisto_05_karttalehti`.

## Examples

Built-in 1:10000 examples write map outputs, terrain reports, LiDAR rasters,
MML line/area diagnostic PDFs, and downloaded source data under
`builds/examples/<name>/`.

| Command | Area | Equivalent `create` command |
| --- | --- | --- |
| `uv run mml-omap ekp` | Espoon keskuspuisto | `uv run mml-omap create 60.188068,24.696799 --name espoo-keskuspuisto --width-km 2.05 --height-km 1.43 --map-title "Espoon keskuspuisto"` |
| `uv run mml-omap kotka-jukola` | Kymi airfield / Kotka-Jukola training-ban area | `uv run mml-omap create 60.589770,26.895951 --name kotka-jukola --width-km 2.8 --height-km 4.1 --map-title "Kotka-Jukola harjoituskieltoalue"` |
| `uv run mml-omap puijo` | Puijo | `uv run mml-omap create 62.909717,27.655838 --name puijo --width-km 1.414 --height-km 1.978 --map-title "Puijo"` |
| `uv run mml-omap vuokatinvaara` | Vuokatinvaara | `uv run mml-omap create 64.131439,28.266418 --name vuokatinvaara --width-km 2.0 --height-km 2.8 --map-title "Vuokatinvaara"` |

Build the same example output set for your own location:

```sh
uv run mml-omap create 60.188068,24.696799 --name my-place --width-km 2.05 --height-km 1.43 --map-title "My Place"
```

This writes to `builds/examples/my-place/` and produces the same 1 m, 2.5 m,
and 5 m contour-interval map variants as the built-in examples. Locations are
entered as the map-center coordinate in latitude,longitude decimal degrees.
If omitted, `--name` defaults to `custom-map` and the map frame defaults to
1 km wide by 1 km high.

Rebuild an example from existing files in its `downloads/` directory without
contacting MML:

```sh
uv run mml-omap ekp --reuse-downloads
uv run mml-omap create 60.188068,24.696799 --name my-place --reuse-downloads
```

Remove generated build artifacts while keeping downloaded source files:

```sh
bash tools/clean-builds.sh
```

Render only the MML line or area diagnostic from an existing downloaded MML zip:

```sh
uv run mml-omap mml-line-diagnostic path/to/source.zip builds/mml-lines.pdf --bbox 371255,6673869,373305,6675299 --scale 10000
uv run mml-omap mml-area-diagnostic path/to/source.zip builds/mml-areas.pdf --bbox 371255,6673869,373305,6675299 --scale 10000
```

## What It Generates

The build output combines:

- MML vector objects: paths, roads, water, marshes, open-land proxies,
  buildings, fences, rocks, MML cliff vectors, and other mapped features.
- MML line and area diagnostic PDFs: raw GeoPackage objects in the same page
  frame as the map, labelled with `kohdeluokka`; red marks objects not
  currently mapped into the orienteering map.
- Point-cloud contours: generated from a continuous ground grid built from
  classified LAZ ground points.
- Point-cloud cliff candidates: generated from steep slope bands in the same
  ground grid.
- Point-cloud vegetation: generated from above-ground point density into ISOM
  `406`/`410` candidate polygons.
- A magnetic-north paper frame with map layout metadata in PNG/PDF output.

The point cloud is downloaded for a slightly larger terrain context bbox and
the final generated features are clipped back to the requested map frame.

## Coordinate System

`mml-omap create` accepts WGS84 latitude,longitude decimal degrees, the same
order commonly copied from Google Maps. Internally, and for lower-level
commands such as `build`, `EPSG:3067` is Finland's standard projected map
coordinate system, `ETRS-TM35FIN`; those coordinates are metric
easting/northing values.

`mml-omap` treats the bbox as the intended paper map frame. It expands the MML
download bbox as needed for magnetic-north rotation, clips output geometry back
to the paper frame, and renders magnetic north upward.

Automatic magnetic correction is a lightweight Finland-only estimate of MML's
`KOK = NEK + NAK`. Use `--magnetic-declination-deg` when you have an
authoritative local value.

The current automatic `NEK` estimate is:

```text
decimal_year = year + day_of_year_elapsed / days_in_year
x = longitude_degrees - 25.0
y = latitude_degrees - 62.0

NEK_degrees =
  11.071507931
  + 0.432817643 * x
  + 0.378133772 * y
  + 0.20 * (decimal_year - 2026.0)
```

`NAK` is calculated as meridian convergence in ETRS-TM35FIN:

```text
NAK_degrees = atan(tan(longitude - 27 degrees) * sin(latitude))
KOK_degrees = NEK_degrees + NAK_degrees
```

The formula is approximate and not an official MML/FMI Erantokartta model.

The same model as a rough Finland heatmap:

![Finland NEK, NAK, and KOK heatmap](docs/finland_nek_nak_kok_heatmap.svg)

## Documentation Map

- [docs/mml-to-isom-mapping.md](docs/mml-to-isom-mapping.md) documents MML
  tables, `kohdeluokka` handling, and ISOM symbol mapping.
- [docs/pipeline.md](docs/pipeline.md) documents the build pipeline, terrain
  model, generated features, algorithms, and known gaps.
- [docs/isom-symbol-library.md](docs/isom-symbol-library.md) documents the
  built-in symbol definitions and renderer contract.

Export the built-in symbol library as JSON:

```sh
uv run mml-omap symbols symbols.json
```

## Notes

Generated GeoJSON is public interchange data, not a proprietary map project.
Maanmittauslaitos data licensing and attribution requirements still apply to
downloaded data.

Generated map files can be large. `.gitignore` excludes local build output,
download archives, GeoPackages, rendered maps, caches, virtual environments,
and `.env` files.
