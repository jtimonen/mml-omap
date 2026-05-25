# Changelog

## 0.1.5

- Renamed built-in example output stems to match the example names:
  `espoo-keskuspuisto`, `kotka-jukola`, `puijo`, and `vuokatinvaara`.
- Added built-in `puijo` and `vuokatinvaara` examples at 1:10000 with A4
  portrait map frames.
- Removed render-time contour fragment merging so PNG/PDF/SVG outputs use the
  contour geometry directly generated from the elevation model.

## 0.1.4

- Added the generated Espoon keskuspuisto PNG as the README demo image.
- PNG map outputs now draw the same title, frame, north marker, and footer
  metadata as PDF/SVG outputs, including paper size, scale, software version,
  contour interval, magnetic declination, and CRS.
- LiDAR point diagnostic PNGs now use the same standard paper size and map
  scale as the actual rendered map, while retaining the version footer and
  viridis height color scale.

## 0.1.3

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

## 0.1.2

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
