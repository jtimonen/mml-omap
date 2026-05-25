# Changelog

## 0.1.2

- Added built-in `ekp` and `kotka-jukola` example builds.
- Built-in examples render at 1:10000.
- Build outputs now use one shared LiDAR point-height diagnostic PNG per source
  dataset.
- LiDAR sheet downloads now consume all returned LAZ/ZIP results for requested
  map sheets.
- Contours are generated from a point-cloud-derived ground model with noise and
  interpolation reporting.
- Vegetation uses fixed low-vegetation and near-ground hit thresholds, filters
  isolated small regions, and avoids drawing green from tall canopy alone.
- Removed symbol-number PDF generation from build outputs.
- Map footers now include the generating `mml-omap` version.
- Map outputs now use the smallest fitting standard A5/A4/A3 paper size in
  portrait or landscape orientation.
- Added pipeline documentation and versioning instructions for future changes.
