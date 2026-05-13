from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
import re
import sys

import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
import numpy as np

from mml_omap.cli import estimate_finland_magnetic_declination_wgs84, meridian_convergence_deg


LON_MIN = 19.0
LON_MAX = 32.2
LAT_MIN = 59.5
LAT_MAX = 70.4
DISPLAY_LATITUDE_DEG = 65.0
DISPLAY_LON_SCALE = math.cos(math.radians(DISPLAY_LATITUDE_DEG))
DATE = dt.date(2026, 1, 1)
BOUNDARY_PATH = Path("docs/finland_boundary.geojson")

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


def load_finland_polygons() -> list[list[list[tuple[float, float]]]]:
    feature = json.loads(BOUNDARY_PATH.read_text(encoding="utf-8"))
    geometry = feature["geometry"]
    if geometry["type"] == "Polygon":
        raw_polygons = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        raw_polygons = geometry["coordinates"]
    else:
        raise ValueError(f"Unsupported boundary geometry: {geometry['type']}")
    return [
        [[(float(lon), float(lat)) for lon, lat in ring] for ring in polygon]
        for polygon in raw_polygons
    ]


def display_lon(longitude_deg: float | np.ndarray) -> float | np.ndarray:
    return (longitude_deg - LON_MIN) * DISPLAY_LON_SCALE + LON_MIN


def display_ring(ring: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    return [display_lon(point[0]) for point in ring], [point[1] for point in ring]


def ring_to_path(ring: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], list[int]]:
    vertices = list(ring)
    codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(vertices) - 2) + [MplPath.CLOSEPOLY]
    return vertices, codes


def compound_path(polygons: list[list[list[tuple[float, float]]]]) -> MplPath:
    vertices: list[tuple[float, float]] = []
    codes: list[int] = []
    for polygon in polygons:
        for ring in polygon:
            ring_vertices, ring_codes = ring_to_path(ring)
            vertices.extend(ring_vertices)
            codes.extend(ring_codes)
    return MplPath(vertices, codes)


def mask_outside_finland(
    lon_grid: np.ndarray,
    lat_grid: np.ndarray,
    polygons: list[list[list[tuple[float, float]]]],
) -> np.ndarray:
    points = np.column_stack([lon_grid.ravel(), lat_grid.ravel()])
    mask = np.zeros(points.shape[0], dtype=bool)
    for polygon in polygons:
        if not polygon:
            continue
        inside = MplPath(polygon[0]).contains_points(points)
        for hole in polygon[1:]:
            inside &= ~MplPath(hole).contains_points(points)
        mask |= inside
    return ~mask.reshape(lon_grid.shape)


def draw_boundaries(ax: plt.Axes, polygons: list[list[list[tuple[float, float]]]]) -> None:
    for polygon in polygons:
        for ring_index, ring in enumerate(polygon):
            x, lat = display_ring(ring)
            ax.plot(x, lat, color="#171717", linewidth=1.0 if ring_index == 0 else 0.55, zorder=5)


def grid_values(
    polygons: list[list[list[tuple[float, float]]]],
) -> tuple[np.ndarray, np.ndarray, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    lon = np.linspace(LON_MIN, LON_MAX, 320)
    lat = np.linspace(LAT_MIN, LAT_MAX, 320)
    lon_grid, lat_grid = np.meshgrid(lon, lat)
    nek = np.vectorize(lambda la, lo: estimate_finland_magnetic_declination_wgs84(float(la), float(lo), DATE))(lat_grid, lon_grid)
    nak = np.vectorize(lambda la, lo: meridian_convergence_deg(float(la), float(lo)))(lat_grid, lon_grid)
    mask = mask_outside_finland(lon_grid, lat_grid, polygons)
    return display_lon(lon_grid), lat_grid, tuple(np.ma.array(values, mask=mask) for values in (nek, nak, nek + nak))


def plot_panel(
    ax: plt.Axes,
    lon_grid: np.ndarray,
    lat_grid: np.ndarray,
    values: np.ndarray,
    polygons: list[list[list[tuple[float, float]]]],
    title: str,
    levels: np.ndarray,
    contour_levels: list[float],
) -> None:
    filled = ax.contourf(lon_grid, lat_grid, values, levels=levels, cmap="viridis", extend="both", antialiased=True)
    contours = ax.contour(lon_grid, lat_grid, values, levels=contour_levels, colors="#1a1a1a", linewidths=0.65, alpha=0.72)
    ax.clabel(contours, fmt="%.0f", fontsize=7, inline=True)
    draw_boundaries(ax, polygons)
    for name, lat, lon in CITY_POINTS:
        x = display_lon(lon)
        ax.plot(x, lat, "o", markersize=2.6, color="#ffffff", markeredgecolor="#111", markeredgewidth=0.75, zorder=6)
        if title.startswith("NEK"):
            ax.text(
                x + 0.05,
                lat + 0.07,
                name,
                fontsize=6.5,
                color="#111",
                zorder=7,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.78, "boxstyle": "round,pad=0.12"},
            )
    ax.set_title(title, fontsize=12, weight="semibold")
    ax.set_xlim(display_lon(LON_MIN), display_lon(LON_MAX))
    ax.set_ylim(LAT_MIN, LAT_MAX)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([display_lon(lon) for lon in [20, 24, 28, 32]])
    ax.set_xticklabels(["20E", "24E", "28E", "32E"])
    ax.set_yticks([60, 64, 68])
    ax.set_yticklabels(["60N", "64N", "68N"])
    ax.grid(color="#d0d0d0", linewidth=0.4, alpha=0.6)
    ax.tick_params(labelsize=7)
    ax.set_xlabel("longitude, scaled by cos(65N)", fontsize=8)
    if title.startswith("NEK"):
        ax.set_ylabel("latitude", fontsize=8)
    return filled


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/finland_nek_nak_kok_heatmap.svg")
    output.parent.mkdir(parents=True, exist_ok=True)
    polygons = load_finland_polygons()
    lon_grid, lat_grid, (nek, nak, kok) = grid_values(polygons)
    fig, axes = plt.subplots(1, 3, figsize=(13.2, 6.0), constrained_layout=True)
    panels = [
        ("NEK / eranto", nek, np.linspace(9.0, 15.2, 18), [10, 11, 12, 13, 14, 15]),
        ("NAK / grid correction", nak, np.linspace(-4.5, 3.2, 18), [-4, -3, -2, -1, 0, 1, 2, 3]),
        ("KOK / total correction", kok, np.linspace(8.4, 16.5, 18), [9, 10, 11, 12, 13, 14, 15, 16]),
    ]
    for ax, (title, values, levels, contour_levels) in zip(axes, panels):
        filled = plot_panel(ax, lon_grid, lat_grid, values, polygons, title, levels, contour_levels)
        colorbar = fig.colorbar(filled, ax=ax, orientation="horizontal", pad=0.07, fraction=0.05)
        colorbar.ax.tick_params(labelsize=7)
        colorbar.set_label("degrees", fontsize=8)
    fig.suptitle("Finland NEK, NAK and KOK heatmaps, 2026-01-01", fontsize=15, weight="semibold")
    fig.text(
        0.5,
        0.01,
        "Boundary: Natural Earth via geo-countries. NEK: least-squares mml-omap approximation from sampled MML Erantokartta values. NAK: EPSG:3067 meridian convergence. KOK = NEK + NAK.",
        ha="center",
        fontsize=8,
    )
    fig.savefig(output, format="svg", metadata={"Date": None})
    plt.close(fig)
    output.write_text(re.sub(r"[ \t]+$", "", output.read_text(encoding="utf-8"), flags=re.MULTILINE), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
