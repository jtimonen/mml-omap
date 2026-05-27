#!/usr/bin/env bash
set -euo pipefail

root="${1:-builds}"

if [[ ! -d "$root" ]]; then
  exit 0
fi

find "$root" \
  -path '*/downloads/*' -prune -o \
  -type f \( \
    -name '*.geojson' -o \
    -name '*.png' -o \
    -name '*.pdf' -o \
    -name '*.svg' -o \
    -name '*-terrain-report.json' \
  \) -delete

find "$root" \
  -path '*/downloads' -prune -o \
  -type d -empty -delete
