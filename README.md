# mml-omap

Generate orienteering-oriented maps from Maanmittauslaitos vector data plus
local LiDAR/DEM-derived terrain inputs.

The normal workflow is one command: download MML topographic vector data,
combine it with a regular ground-elevation XYZ grid and LAS/LAZ point cloud,
then write GeoJSON, PNG, PDF, and a symbol-number PDF.

This is alpha software. It can produce a useful generated base map and terrain
candidate layers, but it is not a field-checked ISOM/ISSprOM production tool.

Coordinates are ETRS-TM35FIN / EPSG:3067 meters, matching the native MML file
service output.

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

`mml-omap build` combines three data sources for the same EPSG:3067 map frame:

- MML vector data downloaded automatically from `maastotietokanta_bbox`.
- A regular ground-elevation XYZ grid, `x y z`, used for contours and cliffs.
- LAS/LAZ or text point rows, `x y z [classification]`, used for vegetation.

The tool does not yet download MML laser scanning point clouds or elevation
model rasters. Prepare the local XYZ and LAS/LAZ inputs with PDAL, GDAL,
LAStools, QGIS, or equivalent tooling before running the build.

## Espoon Keskuspuisto Example

This is centered on the MapAnt location `60.1880680, 24.6967986`. The bbox is
`371255,6673869,373305,6675299` in EPSG:3067 meters.

All generated files go under `builds/examples/espoo-keskuspuisto/`, which is
easy to gitignore or delete.

```sh
uv run mml-omap build builds/examples/espoo-keskuspuisto/mapant-center 371255,6673869,373305,6675299 builds/examples/espoo-keskuspuisto/lidar-ground.xyz builds/examples/espoo-keskuspuisto/laser.laz
```

The command writes:

- `builds/examples/espoo-keskuspuisto/mapant-center.geojson`
- `builds/examples/espoo-keskuspuisto/mapant-center.png`
- `builds/examples/espoo-keskuspuisto/mapant-center.pdf`
- `builds/examples/espoo-keskuspuisto/mapant-center-symbols.pdf`
- `builds/examples/espoo-keskuspuisto/downloads/`

## What It Generates

The build output combines:

- MML vector objects: paths, roads, water, marshes, open-land proxies,
  buildings, fences, rocks, MML cliff vectors, and other mapped features.
- DEM/LiDAR contours: generated from the regular XYZ height grid with index
  contours.
- DEM/LiDAR cliff candidates: generated from steep slope bands in the same
  height grid.
- LiDAR vegetation: generated from above-ground point density into ISOM
  `406`/`410` candidate vegetation polygons.
- A magnetic-north paper frame with map layout metadata in PNG/PDF output.

Green vegetation comes from the LiDAR point-density pipeline so runnable forest
can remain white.

## Coordinate System

`EPSG:3067` is Finland's standard projected map coordinate system,
`ETRS-TM35FIN`. Coordinates are metric easting/northing values, not
latitude/longitude degrees.

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

## Documentation Map

- [docs/mml-to-isom-mapping.md](docs/mml-to-isom-mapping.md) documents MML
  tables, `kohdeluokka` handling, and ISOM symbol mapping.
- [docs/orienteering-map-style-gap.md](docs/orienteering-map-style-gap.md)
  documents why plain vector data is not enough and explains the contour,
  cliff, elevation, and vegetation algorithms.
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
