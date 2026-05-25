# Pipeline

This document describes what `mml-omap build` actually does: data sources,
terrain modelling, generated features, algorithms, and known gaps.

## Data Sources

The primary build path uses:

- MML `maastotietokanta_bbox` vector data for roads, paths, water, marshes,
  buildings, fences, rocks, open-land proxies, and other mapped objects.
- MML `laserkeilausaineisto_05_karttalehti` LAZ point clouds for contours,
  candidate cliffs, and vegetation candidates.

MML source contour vectors are not used by the primary build path.

## Data Fetch

The build expands the requested map frame by `--terrain-context-margin-m` before
resolving MML laser scanning map sheets with `tm35fin`. The 3 km sheet size is
part of MML's LAZ distribution grid, not a contour scale or cartographic
generalization setting.

It downloads every LAZ/ZIP result returned for the covering sheets, builds
terrain candidates from that expanded context, and clips final GeoJSON back to
the requested paper frame.

Each render writes a `*-terrain-report.json` file next to the map outputs. The
report includes the contour interval, downloaded laser sheets, point-cloud grid
parameters, global and per-cell noise estimates, interpolation distances, and
generated feature counts.

Each render also writes `*-lidar-points.png`, a diagnostic image of the point
cloud inside the terrain context bbox. Every observed point is plotted at its
map position and colored by height with the viridis color ramp. The terrain
report records the image path, pixel size, point count, and height range used
for coloring.

## Ground Model

Ground modelling uses classified ground points (`classification == 2`) from the
LAZ files. Points are bucketed onto a regular grid at
`--ground-cell-size-m`. Each grid cell stores the configured ground elevation
quantile, median by default, so multiple points in the same cell become one
terrain estimate instead of forcing the surface through every return.

Empty cells are filled from nearby ground cells with inverse-distance weighting
from a KD-tree search. The model is always completed across the requested map
frame when there are classified ground points in the area. Cells farther than
the nominal fill distance are still interpolated, but they get lower confidence
and are counted in the terrain report instead of falling back to a coarser
elevation product.

The model is treated as noisy observations of a latent ground surface:

```text
elevation_observation(x, y) = mu(x, y) + epsilon
```

Within each grid cell, repeated ground returns are used to estimate both the
cell's elevation observation and its local noise. Noise is estimated robustly
from absolute residuals around the cell estimate. The grid is then smoothed with
a Gaussian kernel using inverse-variance confidence weights, producing the
estimated mean surface `mu(x, y)`.

`mu(x, y)` is represented as a regular raster surface. Elevation at arbitrary
locations inside the map can be evaluated by interpolation on that surface, and
contours are generated from that estimated surface rather than from raw point
elevations.

## Contours

Contours use the point-cloud-derived ground grid. The implementation builds a
2D elevation matrix and uses `contourpy` to generate isolines at the requested
contour interval.

The intentional simplification is in the ground model: multiple returns become
one continuous estimated surface, and empty cells are interpolated. No extra
cartographic line simplification is applied by default. Every
`--index-contour-every` contour is written as ISOM `102` index contour; the
others are ISOM `101`.

## Cliffs

Cliffs use the same ground grid. The implementation estimates slope at each
grid point from central height differences in x/y, converts slope to degrees,
then contours the slope field at `--slope-threshold-deg`.

Segments shorter than `--min-cliff-length-m` are discarded and the remaining
lines are written as candidate ISOM `202` cliffs. This is a candidate extractor:
final passability, teeth, and cartographic displacement still need review.

## Vegetation

Vegetation uses the same LAS/LAZ point rows. Points are bucketed into square
cells. Ground height is estimated from classified ground points where available,
otherwise from local minima.

The classifier follows the same basic threshold shape as Karttapullautin:
count green hits in a low vegetation band and compare them to near-ground hits
with fixed global ratio thresholds. The default near-ground limit is 0.8 m, the
default green-hit band is 0.8-5.0 m, and the default ratio thresholds are 0.68
for ISOM `406` and 1.13 for ISOM `410`. Minimum hit counts are also required to
avoid isolated-noise cells. Tall canopy returns do not by themselves create
green areas. Adjacent cells of the same class are dissolved into polygons with
Shapely before rendering.

## Known Gaps

The generated output is not a field-checked orienteering map. The main known
gaps are:

- path distinctness, surface, width, and seasonal usability are not reliably
  encoded in MML `tieviiva.kohdeluokka`
- vegetation thresholds are not calibrated against local field observations,
  multispectral imagery, or forest inventory rasters
- low undergrowth, visibility, and runnability are not modelled separately
- form lines, depressions, knolls, and other landform interpretation are not
  generated
- candidate cliffs are not classified into final passable/impassable cliff
  symbols, and cliff teeth are not optimized
- boulders, boulder clusters, stony ground, and bare-rock detail are not
  inferred from point-cloud structure
- uncrossable marsh, impassable fences/walls, paved areas, and private/out of
  bounds areas still need better source interpretation
- symbol conflict resolution, minimum-gap enforcement, and print-quality
  overprint behavior are still limited
- there is no field-check workflow or manual correction layer yet
