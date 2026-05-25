# ISOM Symbol Library

`mml-omap` keeps its own structured symbol library in
`src/mml_omap/symbols.py`. It is the renderer contract for converting
symbolized GeoJSON into SVG, PNG, and PDF output.

The library follows the recommendation to model ISOM symbols as structured
cartographic rules instead of treating them as a folder of SVG icons. Many ISOM
symbols are procedural map symbols: dashed lines, cased lines, screen fills,
repeated pattern strokes, point marks, render-order rules, and scale-dependent
minimum sizes. A JSON-like symbol definition is easier to render consistently
to multiple output formats than a copied vector image.

## Sources

Primary references:

- Local checked-in specification: `reference/isom2024.pdf`
- Current online symbol reference:
  https://omapwiki.orienteering.sport/specifications/isom/

The online reference currently identifies the standard as ISOM 2017-2 Revision
6 from January 2024. The built-in library records that source metadata when it
is exported.

## Asset And License Policy

The built-in definitions are maintained locally from the public IOF/O-Map Wiki
symbol specifications. Do not import or copy OpenOrienteering Mapper, OCAD, or
other mapper symbol-set geometry into this repository unless the licensing is
first made explicit and compatible with this MIT-licensed project.

OpenOrienteering Mapper is useful as an external editor and reference workflow,
but its symbol files are not used as source assets here. This keeps the
GeoJSON-to-PDF/PNG pipeline independent of GPL/proprietary symbol libraries.

## Export

Write the built-in library to JSON:

```sh
uv run mml-omap symbols symbols.json
```

Or print it to stdout:

```sh
uv run mml-omap symbols
```

Each entry includes:

- internal render symbol name, such as `major_road` or `swamp`
- ISOM symbol number and name when one is assigned
- geometry type: `point`, `line`, or `area`
- render order
- renderer style parameters in millimetres where applicable

Generated GeoJSON still uses ISOM symbol numbers in `properties.symbol`; the
renderer maps those numbers back to the internal render symbol definitions.
This applies to both MML GeoPackage-derived features and LiDAR/DEM-derived
terrain features.
