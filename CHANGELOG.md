# Changelog

## mml-omap 0.2

### 0.2.0

- Replaced the old top-level LiDAR point PNGs with organized
  `lidar-rasters/` support images.
- Added LaserScan-style LiDAR raster PNGs: median point height, dominant return
  type per 1 m cell, ground and surface elevation/shading/slope views, ground
  coverage and vegetation height.
- Continuous LiDAR color rasters now include an in-image color-scale legend.
- Terrain reports now include the new `lidar_rasters` report block.
- Added `--reuse-downloads` for `build` and the built-in examples.
- Added `tools/clean-builds.sh` to remove generated build artifacts while
  preserving downloaded source files under `downloads/`.
- Mapped MML airfield and runway area tables to ISOM `401` Open land so the
  Kymi airfield example does not render as edge-only linework.
- Moved LiDAR classification helpers into `src/mml_omap/lidar.py` and raster
  generation into `src/mml_omap/lidar_rasters.py`.
- Replaced point-dictionary terrain generation with a continuous
  `GroundModel` used by contours, cliff candidates, and LiDAR rasters.
- Split self-intersecting generated contours into simple line parts instead of
  emitting invalid contour LineStrings.
- Removed closed generated contour rings that enclose less than 10 m2.
- Added `*-mml-lines.svg`, a raw GeoPackage line-object diagnostic labelled by
  `kohdeluokka`, plus the standalone `mml-line-diagnostic` command.
- Shifted the Kotka-Jukola example north and the Vuokatinvaara example south.

## mml-omap 0.1

### 0.1.9

- Bumped the package release version to `0.1.9`.
- Documented that terrain noise estimates use a 0.15 m fallback floor when
  LiDAR ground-cell residuals do not contain enough spread to estimate noise
  from the point cloud itself.

### 0.1.8

- Sports and recreation areas from MML `urheilujavirkistysalue` now render as
  ISOM `401` Open land instead of outline-only/black fallback areas.
- Reduced the built-in Puijo example bbox to about half its previous area while
  keeping it on A4 at 1:10000.

### 0.1.7

- Added an always-generated `*-lidar-return-types.png` diagnostic image that
  colors LiDAR points by return class group: ground, water, low/medium/high
  vegetation, building, noise, and other.
- Kept the existing `*-lidar-points.png` height diagnostic as an all-return
  height-colored point cloud.
- Terrain reports now include both LiDAR diagnostic image reports.
- Documented the return class groups and diagnostic colors.

### 0.1.6

- Vegetation extraction now excludes water, building, noise, bridge-deck, and
  high-noise LiDAR classes before counting runnability candidates.
- Vegetation green-hit counting now uses LAS vegetation classes `3`, `4`, and
  `5`, plus unclassified candidate classes `0` and `1` only after the same
  height-above-ground checks.
- Documented the LiDAR class filtering used by vegetation extraction.

### 0.1.5

- Renamed built-in example output stems to match the example names:
  `espoo-keskuspuisto`, `kotka-jukola`, `puijo`, and `vuokatinvaara`.
- Added built-in `puijo` and `vuokatinvaara` examples at 1:10000 with A4
  portrait map frames.
- Removed render-time contour fragment merging so PNG/PDF/SVG outputs use the
  contour geometry directly generated from the elevation model.

### 0.1.4

- Added the generated Espoon keskuspuisto PNG as the README demo image.
- PNG map outputs now draw the same title, frame, north marker, and footer
  metadata as PDF/SVG outputs, including paper size, scale, software version,
  contour interval, magnetic declination, and CRS.
- LiDAR point diagnostic PNGs now use the same standard paper size and map
  scale as the actual rendered map, while retaining the version footer and
  viridis height color scale.

### 0.1.3

- Built-in examples render at 1:10000.
- Contour geometry smoothing is disabled to avoid introducing contour
  self-intersections in dense terrain detail.
- Post-contour line simplification is removed; contour geometry now comes
  directly from the estimated elevation surface.
- PNG rendering now honors dashed line styles for paths, tracks, and other
  dashed symbols.
- Map footers now include the generating `mml-omap` version.
- Map outputs now use the smallest fitting standard A5/A4/A3 paper size in
  portrait or landscape orientation.
- LiDAR point diagnostic PNGs now include margins, software version text, and a
  labeled viridis height color scale.
- Documented the airborne LiDAR measurement technology used by MML point
  clouds and how the same point cloud is used for ground and vegetation.
- Added versioning and changelog maintenance instructions for future changes.

### 0.1.2

- Added built-in `ekp` and `kotka-jukola` example builds.
- Build outputs now use one shared LiDAR point-height diagnostic PNG per source
  dataset.
- LiDAR sheet downloads now consume all returned LAZ/ZIP results for requested
  map sheets.
- Contours are generated from a point-cloud-derived ground model with noise and
  interpolation reporting.
- Vegetation uses fixed low-vegetation and near-ground hit thresholds, filters
  isolated small regions, and avoids drawing green from tall canopy alone.
- Removed symbol-number PDF generation from build outputs.
- Added pipeline documentation and versioning instructions for future changes.
