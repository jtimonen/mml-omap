"""Internal ISOM-oriented symbol definitions used by mml-omap.

The definitions in this module are a structured, local renderer model derived
from the public IOF ISOM 2017-2 specification and O-Map Wiki entries. They are
not copied from OpenOrienteering Mapper, OCAD, or another symbol-set asset.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ISOM_SPECIFICATION_SOURCE = {
    "standard": "ISOM 2017-2 Revision 6",
    "published": "January 2024",
    "local_reference": "reference/isom2024.pdf",
    "online_reference": "https://omapwiki.orienteering.sport/specifications/isom/",
    "asset_policy": (
        "Renderer parameters are maintained locally from the public standard. "
        "Do not import GPL/proprietary mapper symbol-set geometry into this MIT-licensed project."
    ),
}

DEFAULT_STYLE: dict[str, Any] = {"stroke": "#444444", "stroke_width_mm": 0.18, "fill": "none"}

ISOM_SYMBOL_LIBRARY: dict[str, dict[str, Any]] = {
    "contour": {
        "iof_symbol_number": "101",
        "iof_symbol_name": "Contour",
        "geometry": "line",
        "render_order": 300,
        "style": {"stroke": "#9b5a28", "stroke_width_mm": 0.14, "fill": "none"},
    },
    "index_contour": {
        "iof_symbol_number": "102",
        "iof_symbol_name": "Index contour",
        "geometry": "line",
        "render_order": 310,
        "style": {"stroke": "#9b5a28", "stroke_width_mm": 0.25, "fill": "none"},
    },
    "form_line": {
        "iof_symbol_number": "103",
        "iof_symbol_name": "Form line",
        "geometry": "line",
        "render_order": 320,
        "style": {"stroke": "#9b5a28", "stroke_width_mm": 0.10, "fill": "none", "dasharray": "1.0 0.5"},
    },
    "depression_contour": {
        "iof_symbol_number": "101",
        "iof_symbol_name": "Contour",
        "geometry": "line",
        "render_order": 330,
        "style": {"stroke": "#9b5a28", "stroke_width_mm": 0.14, "fill": "none"},
    },
    "cliff": {
        "iof_symbol_number": "202",
        "iof_symbol_name": "Cliff",
        "geometry": "line",
        "render_order": 440,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.35, "fill": "none"},
    },
    "mapped_rock": {
        "iof_symbol_number": "204",
        "iof_symbol_name": "Boulder",
        "geometry": "point",
        "render_order": 600,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.10, "fill": "#000000", "point_radius_mm": 0.20},
    },
    "open_rock": {
        "iof_symbol_number": "214",
        "iof_symbol_name": "Bare rock",
        "geometry": "area",
        "render_order": 130,
        "style": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#d9d9d9"},
    },
    "lake": {
        "iof_symbol_number": "301",
        "iof_symbol_name": "Uncrossable body of water",
        "geometry": "area",
        "render_order": 140,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.10, "fill": "#b9e3f7"},
    },
    "river": {
        "iof_symbol_number": "301",
        "iof_symbol_name": "Uncrossable body of water",
        "geometry": "area",
        "render_order": 160,
        "style": {"stroke": "#008fd5", "stroke_width_mm": 0.30, "fill": "none"},
    },
    "wide_stream": {
        "iof_symbol_number": "304",
        "iof_symbol_name": "Crossable watercourse",
        "geometry": "line",
        "render_order": 365,
        "style": {"stroke": "#008fd5", "stroke_width_mm": 0.30, "fill": "none"},
    },
    "stream": {
        "iof_symbol_number": "305",
        "iof_symbol_name": "Small crossable watercourse",
        "geometry": "line",
        "render_order": 360,
        "style": {"stroke": "#008fd5", "stroke_width_mm": 0.18, "fill": "none"},
    },
    "swamp": {
        "iof_symbol_number": "308",
        "iof_symbol_name": "Marsh",
        "geometry": "area",
        "render_order": 150,
        "style": {
            "stroke": "none",
            "stroke_width_mm": 0.0,
            "fill": "none",
            "svg_fill_pattern": "marsh",
            "pattern_stroke": "#008fd5",
            "pattern_stroke_width_mm": 0.12,
            "pattern_spacing_mm": 1.0,
            "pattern_dasharray": "1.4 0.55",
        },
    },
    "field": {
        "iof_symbol_number": "401",
        "iof_symbol_name": "Open land",
        "geometry": "area",
        "render_order": 100,
        "style": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#f2c84b"},
    },
    "thick_forest": {
        "iof_symbol_number": "406",
        "iof_symbol_name": "Vegetation: slow running",
        "geometry": "area",
        "render_order": 110,
        "style": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#49a64a"},
    },
    "very_thick_forest": {
        "iof_symbol_number": "410",
        "iof_symbol_name": "Vegetation: fight",
        "geometry": "area",
        "render_order": 120,
        "style": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#16702f"},
    },
    "cultivated_land": {
        "iof_symbol_number": "412",
        "iof_symbol_name": "Cultivated land",
        "geometry": "area",
        "render_order": 102,
        "style": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#f2c84b", "svg_fill_pattern": "cultivated_land"},
    },
    "major_road": {
        "iof_symbol_number": "502",
        "iof_symbol_name": "Wide road",
        "geometry": "line",
        "render_order": 400,
        "style": {
            "stroke": "#000000",
            "stroke_width_mm": 0.58,
            "inner_stroke": "#b68a57",
            "inner_stroke_width_mm": 0.30,
            "fill": "none",
        },
    },
    "road": {
        "iof_symbol_number": "503",
        "iof_symbol_name": "Road",
        "geometry": "line",
        "render_order": 405,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.35, "fill": "none"},
    },
    "small_road": {
        "iof_symbol_number": "504",
        "iof_symbol_name": "Vehicle track",
        "geometry": "line",
        "render_order": 410,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.25, "fill": "none", "dasharray": "3.0 0.75"},
    },
    "path": {
        "iof_symbol_number": "505",
        "iof_symbol_name": "Footpath",
        "geometry": "line",
        "render_order": 420,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.18, "fill": "none", "dasharray": "1.5 0.5"},
    },
    "small_path": {
        "iof_symbol_number": "506",
        "iof_symbol_name": "Small footpath",
        "geometry": "line",
        "render_order": 425,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.18, "fill": "none", "dasharray": "0.75 0.5"},
    },
    "railway": {
        "iof_symbol_number": "509",
        "iof_symbol_name": "Railway",
        "geometry": "line",
        "render_order": 415,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.28, "fill": "none", "dasharray": "2.0 1.0"},
    },
    "fence": {
        "iof_symbol_number": "516",
        "iof_symbol_name": "Fence",
        "geometry": "line",
        "render_order": 430,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.18, "fill": "none"},
    },
    "private_yard": {
        "iof_symbol_number": "520",
        "iof_symbol_name": "Area that shall not be entered",
        "geometry": "area",
        "render_order": 108,
        "style": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#b7bf63"},
    },
    "building": {
        "iof_symbol_number": "521",
        "iof_symbol_name": "Building",
        "geometry": "area",
        "render_order": 500,
        "style": {"stroke": "#000000", "stroke_width_mm": 0.10, "fill": "#222222"},
    },
    "place_label": {
        "iof_symbol_number": None,
        "iof_symbol_name": None,
        "geometry": "point",
        "render_order": 700,
        "style": {"stroke": "none", "stroke_width_mm": 0.0, "fill": "#000000", "font_size_mm": 3.0},
    },
    "water_label": {
        "iof_symbol_number": None,
        "iof_symbol_name": None,
        "geometry": "point",
        "render_order": 700,
        "style": {
            "stroke": "none",
            "stroke_width_mm": 0.0,
            "fill": "#008fd5",
            "font_size_mm": 3.0,
            "font_style": "italic",
        },
    },
}

SYMBOL_STYLES: dict[str, dict[str, Any]] = {
    key: dict(value["style"]) for key, value in ISOM_SYMBOL_LIBRARY.items()
}

IOF_SYMBOLS: dict[str, tuple[str, str]] = {
    key: (str(value["iof_symbol_number"]), str(value["iof_symbol_name"]))
    for key, value in ISOM_SYMBOL_LIBRARY.items()
    if value.get("iof_symbol_number")
}

IOF_NUMBER_TO_RENDER_SYMBOL: dict[str, str] = {}
for render_symbol, metadata in ISOM_SYMBOL_LIBRARY.items():
    number = metadata.get("iof_symbol_number")
    if number and str(number) not in IOF_NUMBER_TO_RENDER_SYMBOL:
        IOF_NUMBER_TO_RENDER_SYMBOL[str(number)] = render_symbol

SYMBOL_RENDER_ORDER: dict[str, int] = {
    key: int(value["render_order"]) for key, value in ISOM_SYMBOL_LIBRARY.items()
}


def iof_symbol_metadata(symbol: str | None) -> dict[str, Any]:
    if not symbol:
        return {"iof_symbol_number": None, "iof_symbol_name": None}
    render_symbol = IOF_NUMBER_TO_RENDER_SYMBOL.get(symbol, symbol)
    metadata = ISOM_SYMBOL_LIBRARY.get(render_symbol)
    if metadata and metadata.get("iof_symbol_number"):
        return {
            "iof_symbol_number": metadata["iof_symbol_number"],
            "iof_symbol_name": metadata["iof_symbol_name"],
        }
    if metadata:
        return {"iof_symbol_number": None, "iof_symbol_name": None}
    return {"iof_symbol_number": None, "iof_symbol_name": "No ISOM feature symbol assigned"}


def export_symbol_library() -> dict[str, Any]:
    return {
        "source": dict(ISOM_SPECIFICATION_SOURCE),
        "symbols": deepcopy(ISOM_SYMBOL_LIBRARY),
    }
