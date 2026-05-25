# Changelog

## 0.1.1

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
