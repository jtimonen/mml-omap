from __future__ import annotations

import datetime as dt
import html
from pathlib import Path
import sys

from mml_omap.cli import estimate_finland_magnetic_declination_wgs84, meridian_convergence_deg


WIDTH = 1200
HEIGHT = 620
PANEL_WIDTH = 330
PANEL_HEIGHT = 500
PANEL_TOP = 58
PANEL_GAP = 34
LEFT = 54
LON_MIN = 19.0
LON_MAX = 32.0
LAT_MIN = 59.5
LAT_MAX = 70.3
DATE = dt.date(2026, 1, 1)

FINLAND_POLYGON = [
    (20.6, 59.8),
    (22.6, 60.0),
    (24.9, 60.0),
    (27.8, 60.3),
    (30.0, 61.0),
    (31.6, 62.4),
    (31.1, 64.0),
    (30.2, 65.0),
    (29.6, 66.2),
    (29.0, 67.2),
    (29.4, 68.3),
    (28.8, 69.1),
    (27.4, 70.1),
    (25.4, 69.8),
    (23.6, 68.8),
    (22.2, 67.4),
    (21.7, 65.9),
    (22.4, 64.6),
    (21.6, 63.4),
    (21.0, 62.2),
    (21.2, 61.2),
    (20.6, 59.8),
]

CITY_POINTS = [
    ("Helsinki", 60.1699, 24.9384),
    ("Turku", 60.4518, 22.2666),
    ("Tampere", 61.4978, 23.7610),
    ("Kuopio", 62.8924, 27.6770),
    ("Lappeenranta", 61.0587, 28.1887),
    ("Ilomantsi", 62.6716, 30.9328),
    ("Rovaniemi", 66.5039, 25.7294),
    ("Ivalo", 68.6560, 27.5390),
]


def point_in_polygon(lon: float, lat: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and lon < (xj - xi) * (lat - yi) / max(yj - yi, 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def project(lon: float, lat: float, panel_x: float) -> tuple[float, float]:
    x = panel_x + (lon - LON_MIN) / (LON_MAX - LON_MIN) * PANEL_WIDTH
    y = PANEL_TOP + (LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * PANEL_HEIGHT
    return x, y


def color(value: float, min_value: float, max_value: float) -> str:
    t = max(0.0, min(1.0, (value - min_value) / (max_value - min_value)))
    if t < 0.5:
        p = t * 2.0
        r = int(51 + (247 - 51) * p)
        g = int(102 + (247 - 102) * p)
        b = int(204 + (247 - 204) * p)
    else:
        p = (t - 0.5) * 2.0
        r = int(247 + (178 - 247) * p)
        g = int(247 + (24 - 247) * p)
        b = int(247 + (43 - 247) * p)
    return f"#{r:02x}{g:02x}{b:02x}"


def field_values(lat: float, lon: float) -> tuple[float, float, float]:
    nek = estimate_finland_magnetic_declination_wgs84(lat, lon, DATE)
    nak = meridian_convergence_deg(lat, lon)
    return nek, nak, nek + nak


def panel(index: int, title: str, value_index: int, value_range: tuple[float, float]) -> list[str]:
    panel_x = LEFT + index * (PANEL_WIDTH + PANEL_GAP)
    lines = [
        f'<text x="{panel_x + PANEL_WIDTH / 2:.1f}" y="31" text-anchor="middle" class="title">{html.escape(title)}</text>',
        f'<rect x="{panel_x:.1f}" y="{PANEL_TOP:.1f}" width="{PANEL_WIDTH}" height="{PANEL_HEIGHT}" fill="#f7f7f4" stroke="#222" stroke-width="1"/>',
    ]
    step_lon = 0.20
    step_lat = 0.20
    lon = LON_MIN
    while lon < LON_MAX:
        lat = LAT_MIN
        while lat < LAT_MAX:
            center_lon = lon + step_lon / 2.0
            center_lat = lat + step_lat / 2.0
            if point_in_polygon(center_lon, center_lat, FINLAND_POLYGON):
                value = field_values(center_lat, center_lon)[value_index]
                x1, y1 = project(lon, lat + step_lat, panel_x)
                x2, y2 = project(lon + step_lon, lat, panel_x)
                lines.append(
                    f'<rect x="{x1:.2f}" y="{y1:.2f}" width="{x2 - x1 + 0.2:.2f}" '
                    f'height="{y2 - y1 + 0.2:.2f}" fill="{color(value, *value_range)}"/>'
                )
            lat += step_lat
        lon += step_lon
    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in (project(lon, lat, panel_x) for lon, lat in FINLAND_POLYGON))
    lines.append(f'<polyline points="{points}" fill="none" stroke="#111" stroke-width="1.4"/>')
    for name, lat, lon in CITY_POINTS:
        x, y = project(lon, lat, panel_x)
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="#111"/>')
        if index == 0:
            lines.append(f'<text x="{x + 5:.1f}" y="{y - 4:.1f}" class="city">{html.escape(name)}</text>')
    return lines


def legend(x: float, y: float, label: str, value_range: tuple[float, float]) -> list[str]:
    lines = [f'<text x="{x:.1f}" y="{y - 8:.1f}" class="legend-label">{html.escape(label)}</text>']
    for i in range(80):
        value = value_range[0] + (value_range[1] - value_range[0]) * i / 79
        lines.append(f'<rect x="{x + i * 2:.1f}" y="{y:.1f}" width="2.2" height="10" fill="{color(value, *value_range)}"/>')
    lines.append(f'<text x="{x:.1f}" y="{y + 27:.1f}" class="legend-value">{value_range[0]:.1f} deg</text>')
    lines.append(f'<text x="{x + 160:.1f}" y="{y + 27:.1f}" text-anchor="end" class="legend-value">{value_range[1]:.1f} deg</text>')
    return lines


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/finland_nek_nak_kok_heatmap.svg")
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        "<style>",
        "text { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; fill: #111; }",
        ".title { font-size: 20px; font-weight: 650; }",
        ".city { font-size: 10px; paint-order: stroke; stroke: white; stroke-width: 3px; }",
        ".caption, .legend-value { font-size: 12px; fill: #333; }",
        ".legend-label { font-size: 13px; font-weight: 600; }",
        "</style>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="600" y="592" text-anchor="middle" class="caption">Approximate Finland heatmaps from mml-omap model, 2026-01-01. NEK is calibrated to sampled MML Erantokartta city values; NAK is EPSG:3067 meridian convergence; KOK = NEK + NAK.</text>',
    ]
    lines.extend(panel(0, "NEK / eranto", 0, (9.5, 15.2)))
    lines.extend(panel(1, "NAK / grid correction", 1, (-4.5, 3.2)))
    lines.extend(panel(2, "KOK / total correction", 2, (8.4, 16.5)))
    lines.extend(legend(84, 540, "NEK", (9.5, 15.2)))
    lines.extend(legend(448, 540, "NAK", (-4.5, 3.2)))
    lines.extend(legend(812, 540, "KOK", (8.4, 16.5)))
    lines.append("</svg>")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
