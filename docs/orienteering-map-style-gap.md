# Orienteering Map Style Gap

MapAnt-style output is better than a plain MML vector render because it is a
terrain-generation pipeline, not only a table-to-symbol lookup. MapAnt Finland
is based on public Finnish laser scanning data and topographic maps, with
Karttapullautin as a core generation step.

The current `mml-omap build` pipeline renders MML vector features as ISOM-like
symbols and derives terrain candidates from MML laser scanning point clouds.
MML vectors cover roads, paths, water, buildings, cliffs that are already in
Maastotietokanta, marshes, and open-land proxies. The point cloud is the source
of truth for generated contours, candidate cliff lines, and candidate
`406`/`410` vegetation polygons.

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

Orienteering map style vegetation requires LiDAR or another density source. The
combined build uses the point-density workflow below:

1. Accept LAS/LAZ or text point rows for the same paper frame.
2. Estimate per-cell ground height from classified ground points or local minima.
3. Classify cells into white, `406`, and `410`-like runnability bands using
   point-density thresholds.
4. Dissolve adjacent vegetation cells into polygons.
5. Clip and render the polygons below point/line symbols.
That is the feature family needed to close the gap with MapAnt. The renderer
also refuses to draw area vegetation symbols as point circles, so malformed
area-as-point vegetation features do not appear as random green dots in white
forest.

## Contours

MML `korkeuskayra` is a vector contour layer, not a continuous elevation model.
It is good enough for a quick basemap preview, but it can contain fragments,
source-data breaks, and edge cuts. From those lines alone the program cannot
guarantee a globally consistent height field where every point's relative
height can be inferred from every other point.

The combined build downloads the MML 0.5 p LAZ map sheets covering a slightly
larger terrain context bbox than the requested map frame. It builds a ground
grid from classified ground points, generates ISOM `101`/`102` contours from
that grid, extracts candidate ISOM `202` cliffs from steep slope bands, and
classifies candidate `406`/`410` vegetation from above-ground point density.

This keeps the height model and point cloud as the source of truth instead of
trying to repair broken source contour or cliff vectors.

## Algorithms

The build expands the map bbox by `--terrain-context-margin-m` before resolving
MML 1:5000 map sheets with `tm35fin`. It downloads
`laserkeilausaineisto_05_karttalehti` LAZ data for those sheets and clips the
final GeoJSON back to the requested paper frame.

Ground modelling uses classified ground points (`classification == 2`) from the
LAZ files. Points are bucketed onto a regular grid at
`--ground-cell-size-m`; each grid cell stores the configured ground elevation
quantile, median by default, so multiple points in the same cell become one
terrain estimate instead of forcing the surface through every return. Empty
cells are filled from nearby ground cells with inverse-distance weighting from a
KD-tree search. If a hole is too large to fill, the build fails instead of
silently falling back to a coarser elevation product.

Contours use that point-cloud-derived ground grid. The implementation builds a
2D elevation matrix, then uses `contourpy` to generate isolines at the requested
contour interval. The intentional simplification is in the ground model:
multiple returns become one continuous estimated surface, and empty cells are
interpolated. No extra cartographic line simplification is applied by default.
Every `--index-contour-every` contour is written as ISOM `102` index contour;
the others are ISOM `101`.

Cliffs use the same ground-elevation grid. The implementation estimates slope at
each grid point from central height differences in x/y, converts slope to
degrees, then contours the slope field at `--slope-threshold-deg`. Resulting
segments shorter than `--min-cliff-length-m` are discarded and the remaining
lines are written as candidate ISOM `202` cliffs. This is a candidate extractor:
final passability, teeth, and cartographic displacement still need review.

Vegetation uses LAS/LAZ or text point rows `x y z [classification]`. Points are
bucketed into square cells. Ground height is estimated from classified ground
points where available, otherwise from local minima. Non-ground points at least
`--min-height-m` above that ground are counted per cell. Cells with at least
`--slow-count` points become ISOM `406`; cells with at least `--fight-count`
points become ISOM `410`. Adjacent cells of the same class are dissolved into
polygons with Shapely before rendering.
