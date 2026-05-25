# Orienteering Map Style Gap

MapAnt-style output is better than a plain MML vector render because it is a
terrain-generation pipeline, not only a table-to-symbol lookup. MapAnt Finland
is based on public Finnish laser scanning data and topographic maps, with
Karttapullautin as a core generation step.

The current `mml-omap` pipeline can render MML vector features as ISOM-like
symbols. That covers roads, paths, water, buildings, cliffs that are already in
Maastotietokanta, marshes, open land proxies, and existing MML contours. It
does not yet derive terrain features from point clouds or rasters.

## Why `tieviiva` Is Weak

MML `tieviiva.kohdeluokka` values classify national topographic road/path
features. They do not directly encode orienteering path distinctness, width in
forest, surface, visibility, seasonal usability, or whether a line should be
generalized away. A direct mapping can only be a first pass.

Better path rendering needs more context:

- all available `tieviiva` attributes, not only `kohdeluokka`
- geometry width, length, junction density, and road hierarchy
- surrounding land cover and buildings
- local comparison against MapAnt, aerial imagery, or field-checked maps
- mapper overrides for ambiguous classes

## Green Vegetation

ISOM green is about reduced runnability and visibility. A forest polygon alone
does not imply green; normal runnable forest is white on an orienteering map.

`--include-forest-mask` exists for experimentation. It maps MML
`metsamaankasvillisuus` polygons to ISOM `406` so the map can show a green
forest proxy. This is useful for visual comparison with MapAnt, but it should
not be treated as finished vegetation mapping.

Orienteering map style vegetation requires LiDAR or another density source. A
future implementation should:

1. Download or accept MML laser scanning point clouds for the same paper frame.
2. Build a ground model and vegetation-height/density rasters.
3. Classify cells into white, `406`, `408`, and `410`-like runnability bands
   using configurable thresholds.
4. Smooth, simplify, and polygonize those rasters.
5. Clip and render the polygons below point/line symbols.
6. Keep all thresholds user-configurable because vegetation response varies by
   LiDAR acquisition, season, terrain type, and forest structure.

That is the feature family needed to close the gap with MapAnt. The current
forest mask is only a safe intermediate step.

## Contours

MML `korkeuskayra` is a vector contour layer, not a continuous elevation model.
It is good enough for a quick basemap preview, but it can contain fragments,
source-data breaks, and edge cuts. From those lines alone the program cannot
guarantee a globally consistent height field where every point's relative
height can be inferred from every other point.

The `contours-from-xyz` command is the first LiDAR-oriented contour path in this
project. It expects a regular ground-elevation XYZ grid produced from LiDAR or
DEM data, then generates ISOM `101` contour GeoJSON from that grid. This keeps
the height model as the source of truth instead of trying to repair broken
source contour vectors.
