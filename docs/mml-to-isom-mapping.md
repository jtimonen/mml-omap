# MML To ISOM Mapping

This file is the explicit contract between MML Maastotietokanta source classes
and the ISOM symbols written to GeoPackage/vector-derived GeoJSON.

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

For every emitted mapped vector feature:

- `symbol`: ISOM symbol number as a string.
- `iof_symbol_number`: same value as `symbol`, kept for explicitness.
- `iof_symbol_name`: ISOM symbol name used by the built-in mapping.
- `source_table`: MML GeoPackage table.
- `kohdeluokka`: MML feature class, when present in the source table.

Features without an ISOM symbol assignment are skipped by default. With
`--include-unmapped`, they may be retained for inspection, but they do not get a
`symbol` property.

## Current Built-In Vector Mapping

| MML table | MML `kohdeluokka` | ISOM symbol | ISOM name | Notes |
| --- | --- | --- | --- | --- |
| `jyrkanne` | `34400` | `201` | Impassable cliff | MML cliff class mapped as impassable cliff. |
| `jyrkanne` | other mapped values | `202` | Cliff | Default MML cliff line where passability is not inferred. |
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
| `lentokenttaalue` | all | `401` | Open land | Airfield area proxy; paved/private access semantics are not inferred. |
| `lentokentankiitotie` | all | `401` | Open land | Runway area proxy so airfields do not render as outline-only gaps. |
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

## Point-Cloud-Derived Terrain Features

The combined build does not derive contours, cliffs, or vegetation from MML
`kohdeluokka` mappings. It writes already-numbered candidate terrain features
from downloaded MML laser scanning point clouds:

| Build component | Input | ISOM output | Notes |
| --- | --- | --- | --- |
| Contours | point-cloud-derived ground grid | `101`, `102` | Uses the requested contour interval, emits every fifth line as index contour by default, and stores `korkeusarvo` in millimetres. |
| Cliffs | point-cloud-derived ground grid | `202` | Candidate cliff lines from steep slope bands; passability and final symbol choice need review. |
| Vegetation | LAS/LAZ points | `406`, `410` | Candidate vegetation polygons from above-ground point density. |

## Explicitly Not Emitted By Default

| MML table | Reason |
| --- | --- |
| `paikannimi` | Place-name labels are not numbered ISOM terrain/object symbols. |
| Any unmapped table | No documented ISOM mapping has been assigned yet. |

## Known Gaps

This mapping is intentionally conservative. The following require more source
data or field interpretation before they can be mapped responsibly:

- field-checked vegetation runnability; the LiDAR pipeline generates candidate
  `406`/`410` polygons, but thresholds are local and need review
- uncrossable marsh `307` versus marsh `308`
- impassable fence/wall symbols
- boulder size classes and boulder clusters
- paved area versus private/out-of-bounds area
- path distinctness and road/track usability
- final point-cloud cliff classification; the LiDAR pipeline generates candidate `202`
  lines from slope bands, but passability and symbol selection still need review

