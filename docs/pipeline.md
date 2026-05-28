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

## LiDAR Source Technology

The build uses MML airborne LiDAR, also called airborne laser scanning. In this
measurement technology, an aircraft-mounted laser scanner emits short laser
pulses toward the ground. The scanner measures the time it takes for reflected
energy to return, and the aircraft position and attitude are tracked with
GNSS/IMU systems. Those measurements are combined into georeferenced 3D points
with x/y/z coordinates.

A single laser pulse can produce multiple returns. In forest, early returns may
come from treetops or branches, while later returns may come from lower
vegetation or the ground if the pulse penetrates the canopy. The delivered LAZ
file is therefore a point cloud of individual measured returns, not a raster
image and not a finished terrain model.

The MML `0.5 p` product is distributed as LAS/LAZ point data and is thinned
from MML's denser `5 p` national laser scanning data without changing
individual point quality attributes. MML describes the data as automatically
classified point clouds intended for height-model and forest-interpretation
use, with point classes such as unclassified points and ground points.

The program does not download separate scans for ground height and vegetation.
Both come from the same LAZ point rows:

- Ground height uses classified ground points, LAS class `2`, to estimate the
  continuous terrain surface `mu(x,y)`.
- Vegetation/runnability uses point heights relative to that local ground
  surface. It compares low green-band hits to near-ground hits with fixed
  thresholds and filters out isolated small regions.

This means vegetation quality depends on scan season, leaf-on/leaf-off
conditions, point density, classification quality, and how many returns
penetrate through canopy to the lower vegetation and ground.

References:

- https://www.maanmittauslaitos.fi/node/13294
- https://www.maanmittauslaitos.fi/laserkeilaus-ja-ilmakuvaus

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

Each source-data build writes LiDAR raster support images under
`lidar-rasters/` next to the generated maps. They use the same standard paper
size, orientation, scale, map frame, and magnetic-north rotation as the rendered
map.

`*-median-point-height.png` colors each occupied 1 m x 1 m cell by the median
raw LiDAR return elevation in that cell. This keeps perceived brightness from
being controlled by local point density.

`*-return-types.png` colors each occupied 1 m x 1 m cell by its dominant LAS
return class group, not by individual overplotted points. The report includes
both point counts and dominant-cell counts for these groups:

| Group | LAS classes | Diagnostic color |
| --- | --- | --- |
| Ground | `2` | brown |
| Water | `9` | blue |
| Low vegetation | `3` | light green |
| Medium vegetation | `4` | green |
| High vegetation | `5` | dark green |
| Building | `6` | dark gray |
| Noise | `7`, `18` | magenta |
| Other | all other or missing classes | gray |

The terrain report stores these files under the `lidar_rasters` key:

| Output suffix | Meaning |
| --- | --- |
| `-ground-elevation.png` | Ground model elevation colored by height in metres. |
| `-ground-shading.png` | Analytical hillshade from the ground model. |
| `-ground-slope.png` | Ground model slope; darker pixels are steeper. |
| `-surface-elevation.png` | Surface elevation from highest observed return over ground, colored by height in metres. |
| `-surface-shading.png` | Analytical hillshade from the surface model. |
| `-surface-slope.png` | Surface slope; darker pixels are steeper. |
| `-ground-coverage.png` | Blue means no point, yellow means points but no ground return, black means ground return present. |
| `-vegetation-height.png` | Maximum vegetation-candidate return height above ground. |

Continuous color rasters include an in-image color-scale legend. These rasters
use 1 m cells, matching the OpenOrienteering LaserScan tool's typical pixel
distance, but the implementation is independent and uses the existing
`mml-omap` ground model and source point rows. They are intended as map-making
support layers; map feature generation still uses the
vector algorithms described below.

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

When a cell has only one usable ground return, or the residuals have effectively
no spread, the robust estimate cannot distinguish point-cloud noise from zero
local variation. In that case the terrain report uses a conservative 0.15 m
noise floor. On sparse 0.5 p data with 1 m grid cells, many cells hit that
fallback, so `global_noise_estimate_m` can remain 0.15 m across builds.

The smoothed grid is wrapped in a continuous `GroundModel`. `mu(x, y)` is the
model's spline interpolation function, so elevation and mathematical gradient
can be evaluated at arbitrary coordinates inside the map frame. The regular
grid is the observation/support grid, not the public contour-generation data
structure.

## Contours

Contours use the point-cloud-derived `GroundModel`. The implementation samples
the continuous `mu(x, y)` surface on the model grid and uses `contourpy` to
generate isolines at the requested contour interval. The sampled grid is used
only as the numerical isoline extraction input; the source of truth for ground
height is the continuous model.

The intentional simplification is only in the mathematical ground model:
multiple returns become one continuous estimated surface, empty cells are
interpolated, and the surface is noise-weighted and smoothed before contour
extraction. The generated contour geometry is taken directly from that
estimated surface, with a small topology-preserving cleanup to remove numerical
self-intersections from very detailed isolines. Every `--index-contour-every`
contour is written as ISOM `102` index contour; the others are ISOM `101`.

## Cliffs

Cliffs use the same `GroundModel`. The implementation evaluates the
mathematical gradient of `mu(x, y)`, converts slope to degrees, then contours
the slope field at `--slope-threshold-deg`.

Segments shorter than `--min-cliff-length-m` are discarded and the remaining
lines are written as candidate ISOM `202` cliffs. This is a candidate extractor:
final passability, teeth, and cartographic displacement still need review.

## Vegetation

Vegetation uses the same LAS/LAZ point rows. Points are bucketed into square
cells. Ground height is estimated from classified ground points, LAS class `2`.
Vegetation density uses only vegetation classes `3`, `4`, and `5`, plus
unclassified candidate returns `0` and `1` when they pass the same
height-above-ground tests. Water, buildings, noise, bridge-deck, and high-noise
classes are excluded before hit counting, so classes such as water `9`,
building `6`, noise `7`, and high noise `18` cannot create green vegetation.

The classifier counts green hits in a low vegetation band and compares them to
near-ground hits with fixed global ratio thresholds. The default near-ground
limit is 0.8 m, the default green-hit band is 0.8-5.0 m, and the default ratio
thresholds are 0.68 for ISOM `406` and 1.13 for ISOM `410`. Minimum hit counts
are also required. Candidate cells are then filtered by continuous region area,
so isolated cells representing one or two trees are not drawn as green. Tall
canopy returns do not by themselves create green areas. Adjacent cells of the
same class are dissolved into polygons with Shapely before rendering.

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
