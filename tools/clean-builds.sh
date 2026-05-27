#!/usr/bin/env bash
set -euo pipefail

root="${1:-builds}"

if [[ ! -d "$root" ]]; then
  exit 0
fi

find "$root" \
  -type f \
  ! -path '*/downloads/*' \
  \( \
    -name '*.geojson' -o \
    -name '*.png' -o \
    -name '*.pdf' -o \
    -name '*.svg' -o \
    -name '*-terrain-report.json' \
  \) -delete

find "$root" \
  -depth \
  -type d \
  ! -path '*/downloads' \
  ! -path '*/downloads/*' \
  -empty -delete
