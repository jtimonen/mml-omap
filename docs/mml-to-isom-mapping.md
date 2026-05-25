# MML To ISOM Mapping

This file is the explicit contract between MML Maastotietokanta source classes
and the ISOM symbols written to generated GeoJSON.

`mml-omap` writes only ISOM symbol numbers to `properties.symbol`. Internal
renderer names such as `major_road`, `path`, or `swamp` are implementation
details and must not appear as GeoJSON symbol values.

The mapping follows the same idea as Karttapullautin's `vectorconf` mechanism:
match a vector source layer and attribute values, then assign an orienteering
symbol number. Orienteering BC documents that convention as a three-column
mapping of human description, ISOM symbol code, and source-attribute predicate.
Their public examples are for Canadian NTDB/GeoGratis data, not Finnish MML
data, so they are used only as prior art for the mapping structure and broad
feature categories. This project's MML-specific values below are maintained
locally.

Primary references:

- MML Maastotietokanta product description and GeoPackage source data.
- IOF ISOM 2017-2 / O-Map Wiki symbol definitions.
- Karttapullautin vectorconf practice, as documented by Orienteering BC.

## GeoJSON Properties

For every emitted mapped feature:

- `symbol`: ISOM symbol number as a string.
- `iof_symbol_number`: same value as `symbol`, kept for explicitness.
- `iof_symbol_name`: ISOM symbol name used by the built-in mapping.
- `source_table`: MML GeoPackage table.
- `kohdeluokka`: MML feature class, when present in the source table.

Features without an ISOM symbol assignment are skipped by default. With
`--include-unmapped`, they may be retained for inspection, but they do not get a
`symbol` property.

## Current Built-In Mapping

| MML table | MML `kohdeluokka` | ISOM symbol | ISOM name | Notes |
| --- | --- | --- | --- | --- |
| `korkeuskayra` | all | `101` | Contour | MML height line. Index contour styling is currently render-time only. |
| `jyrkanne` | all | `202` | Cliff | MML cliff line. Impassable/passable distinction is not inferred yet. |
| `kallioalue` | all | `214` | Bare rock | MML rock area. This is a source-data proxy, not field-checked bare rock. |
| `kivi` | all | `204` | Boulder | MML point rock. Size classes are not inferred yet. |
| `jarvi` | all | `301` | Uncrossable body of water | Lake/body of water polygon. |
| `meri` | all | `301` | Uncrossable body of water | Sea/water polygon. |
| `virtavesialue` | all | `301` | Uncrossable body of water | Wide river/stream polygon. |
| `virtavesikapea` | `36312` | `304` | Crossable watercourse | Wider narrow-watercourse class. |
| `virtavesikapea` | other mapped values | `305` | Small crossable watercourse | Default narrow watercourse line. |
| `suo` | all | `308` | Marsh | Crossable marsh by default. Uncrossable marsh is not inferred yet. |
| `soistuma` | all | `308` | Marsh | Treated as marsh proxy. |
| `maatalousmaa` | all | `412` | Cultivated land | Crop state/runnability is not known from MML alone. |
| `niitty` | all | `401` | Open land | Open-land proxy. |
| `muuavoinalue` | all | `401` | Open land | Open-land proxy. |
| `puisto` | all | `401` | Open land | Open-land proxy; may be wrong for wooded parks. |
| `tieviiva` | `12111`, `12112`, `12121`, `12122`, `12131`, `12132` | `502` | Wide road | Mapped as wide road with black edges and brown infill. |
| `tieviiva` | `12141`, `12142` | `503` | Road | Small driveable road proxy. |
| `tieviiva` | `12151`, `12152` | none | none | Ferry/lossi transport lines are not rendered as terrain roads. |
| `tieviiva` | `12314` | `503` | Road | MML walking/cycle way, usually road-like or paved in map-reading terms. |
| `tieviiva` | `12316` | `504` | Vehicle track | MML driving path / track proxy. |
| `tieviiva` | `12313` | `505` | Footpath | MML path. |
| `tieviiva` | `12311`, `12312` | `506` | Small footpath | Legacy/seasonal path-like proxy; `12312` is winter road and may not be visible in summer. |
| `tieviiva` | `12315`, `12317` | `506` | Small footpath | Smaller or less prominent path proxy. |
| `rautatie` | all | `509` | Railway | Railway line. |
| `aita` | all | `516` | Fence | Passable/crossable fence by default. Impassable fence is not inferred yet. |
| `rakennus` | all | `521` | Building | Building polygon. |
| `rakennusreunaviiva` | all | `521` | Building | Building outline linework. |
| `taajaanrakennettualue` | all | `520` | Area that shall not be entered | Broad proxy for private/built-up area; not field-checked. |

## Optional Forest Mask

`--include-forest-mask` adds `metsamaankasvillisuus` polygons as ISOM `406`
(`Vegetation: slow running`). This is intentionally opt-in. The MML forest
polygon says that the area is forested; it does not measure orienteering
runnability, visibility, undergrowth, or passability. Treat it as a visual
green mask for experiments and background-map comparison, not as a
field-checked ISOM vegetation classification.

Orienteering map style green needs laser scanning or another vegetation-density
source with tuned classification heuristics, not this single vector land-cover
class.

## Explicitly Not Emitted By Default

| MML table | Reason |
| --- | --- |
| `paikannimi` | Place-name labels are not numbered ISOM terrain/object symbols. |
| `urheilujavirkistysalue` | MML recreation/sports land use is too broad to assign a reliable ISOM symbol automatically. |
| Any unmapped table | No documented ISOM mapping has been assigned yet. |

## Known Gaps

This mapping is intentionally conservative. The following require more source
data or field interpretation before they can be mapped responsibly:

- vegetation runnability symbols `406`-`410`
- uncrossable marsh `307` versus marsh `308`
- impassable fence/wall symbols
- boulder size classes and boulder clusters
- paved area versus private/out-of-bounds area
- path distinctness and road/track usability
- cliffs derived from laser scanning or slope analysis

