"""LiDAR diagnostic raster rendering."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from . import __version__


DIAGNOSTIC_CELL_SIZE_M = 1.0


def diagnostic_plot_geometry(transform: Any, dpi: int) -> dict[str, int | float]:
    px_per_mm = dpi / 25.4
    plot_left = int(round(transform.map_left_mm * px_per_mm))
    plot_top = int(round(transform.map_top_mm * px_per_mm))
    plot_width = max(1, int(round(transform.map_width_mm * px_per_mm)))
    plot_height = max(1, int(round(transform.map_height_mm * px_per_mm)))
    return {
        "px_per_mm": px_per_mm,
        "width": max(1, int(round(transform.page_width_mm * px_per_mm))),
        "height": max(1, int(round(transform.page_height_mm * px_per_mm))),
        "plot_left": plot_left,
        "plot_top": plot_top,
        "plot_width": plot_width,
        "plot_height": plot_height,
        "plot_right": plot_left + plot_width,
        "plot_bottom": plot_top + plot_height,
    }


def diagnostic_grid_shape(transform: Any) -> tuple[int, int]:
    columns = max(1, int(math.ceil((transform.max_x - transform.min_x) / DIAGNOSTIC_CELL_SIZE_M)))
    rows_count = max(1, int(math.ceil((transform.max_y - transform.min_y) / DIAGNOSTIC_CELL_SIZE_M)))
    return columns, rows_count


def diagnostic_cell_bounds(transform: Any, column: int, row: int) -> tuple[float, float, float, float]:
    x0 = transform.min_x + column * DIAGNOSTIC_CELL_SIZE_M
    y0 = transform.min_y + row * DIAGNOSTIC_CELL_SIZE_M
    return (
        x0,
        y0,
        min(x0 + DIAGNOSTIC_CELL_SIZE_M, transform.max_x),
        min(y0 + DIAGNOSTIC_CELL_SIZE_M, transform.max_y),
    )


def paint_cell(image: Any, transform: Any, metrics: dict[str, int | float], column: int, row: int, color: Any) -> bool:
    x0, y0, x1, y1 = diagnostic_cell_bounds(transform, column, row)
    left, top = transform.to_mm([x0, y1])
    right, bottom = transform.to_mm([x1, y0])
    px_per_mm = float(metrics["px_per_mm"])
    px0 = max(int(metrics["plot_left"]), int(math.floor(min(left, right) * px_per_mm)))
    px1 = min(int(metrics["plot_right"]), int(math.ceil(max(left, right) * px_per_mm)))
    py0 = max(int(metrics["plot_top"]), int(math.floor(min(top, bottom) * px_per_mm)))
    py1 = min(int(metrics["plot_bottom"]), int(math.ceil(max(top, bottom) * px_per_mm)))
    if px1 <= px0 or py1 <= py0:
        return False
    image[py0:py1, px0:px1, 0:3] = color
    image[py0:py1, px0:px1, 3] = 255
    return True


def draw_frame(image: Any, metrics: dict[str, int | float]) -> None:
    plot_left = int(metrics["plot_left"])
    plot_top = int(metrics["plot_top"])
    plot_right = int(metrics["plot_right"])
    plot_bottom = int(metrics["plot_bottom"])
    image[plot_top, plot_left:plot_right, 0:3] = 0
    image[plot_bottom - 1, plot_left:plot_right, 0:3] = 0
    image[plot_top:plot_bottom, plot_left, 0:3] = 0
    image[plot_top:plot_bottom, plot_right - 1, 0:3] = 0


def grid_array_from_xyz_grid(xs: list[float], ys: list[float], points: dict[tuple[float, float], float]) -> Any:
    import numpy as np

    return np.asarray([[points[(x, y)] for x in xs] for y in ys], dtype=float)


def nearest_grid_values_for_cells(
    xs: list[float],
    ys: list[float],
    grid: Any,
    transform: Any,
    *,
    columns: int,
    rows_count: int,
) -> Any:
    import numpy as np

    center_x = transform.min_x + (np.arange(columns, dtype=float) + 0.5) * DIAGNOSTIC_CELL_SIZE_M
    center_y = transform.min_y + (np.arange(rows_count, dtype=float) + 0.5) * DIAGNOSTIC_CELL_SIZE_M
    xs_array = np.asarray(xs, dtype=float)
    ys_array = np.asarray(ys, dtype=float)
    x_indexes = np.searchsorted(xs_array, center_x)
    y_indexes = np.searchsorted(ys_array, center_y)
    x_indexes = np.clip(x_indexes, 1, len(xs_array) - 1)
    y_indexes = np.clip(y_indexes, 1, len(ys_array) - 1)
    x_indexes = np.where(
        np.abs(center_x - xs_array[x_indexes - 1]) <= np.abs(center_x - xs_array[x_indexes]),
        x_indexes - 1,
        x_indexes,
    )
    y_indexes = np.where(
        np.abs(center_y - ys_array[y_indexes - 1]) <= np.abs(center_y - ys_array[y_indexes]),
        y_indexes - 1,
        y_indexes,
    )
    return grid[np.ix_(y_indexes, x_indexes)]


def diagnostic_surfaces(source_data: dict[str, Any], transform: Any) -> dict[str, Any]:
    import numpy as np

    from .cli import (
        LIDAR_GROUND_CLASS,
        lidar_return_can_count_as_green,
        normalize_lidar_classification,
    )

    columns, rows_count = diagnostic_grid_shape(transform)
    ground_model = nearest_grid_values_for_cells(
        source_data["xs"],
        source_data["ys"],
        grid_array_from_xyz_grid(source_data["xs"], source_data["ys"], source_data["elevation_points"]),
        transform,
        columns=columns,
        rows_count=rows_count,
    )
    point_count = np.zeros((rows_count, columns), dtype=np.uint16)
    ground_count = np.zeros((rows_count, columns), dtype=np.uint16)
    object_count_le5 = np.zeros((rows_count, columns), dtype=np.uint16)
    min_object_height = np.full((rows_count, columns), np.nan, dtype=float)
    vegetation_height = np.full((rows_count, columns), np.nan, dtype=float)
    max_observed_z = np.full((rows_count, columns), -np.inf, dtype=float)

    rows_array = np.asarray(source_data["lidar_rows"], dtype=float)
    if rows_array.ndim != 2 or rows_array.shape[1] < 4:
        raise ValueError("Point rows must contain x, y, z, classification")
    mask = (
        np.isfinite(rows_array[:, 0])
        & np.isfinite(rows_array[:, 1])
        & np.isfinite(rows_array[:, 2])
        & (rows_array[:, 0] >= transform.min_x)
        & (rows_array[:, 0] <= transform.max_x)
        & (rows_array[:, 1] >= transform.min_y)
        & (rows_array[:, 1] <= transform.max_y)
    )
    points = rows_array[mask]
    if len(points):
        column_indices = np.clip(
            np.floor((points[:, 0] - transform.min_x) / DIAGNOSTIC_CELL_SIZE_M).astype(int),
            0,
            columns - 1,
        )
        row_indices = np.clip(
            np.floor((points[:, 1] - transform.min_y) / DIAGNOSTIC_CELL_SIZE_M).astype(int),
            0,
            rows_count - 1,
        )
        classifications = np.asarray(
            [
                normalize_lidar_classification(int(value)) if np.isfinite(value) else -1
                for value in points[:, 3]
            ],
            dtype=int,
        )
        heights = points[:, 2] - ground_model[row_indices, column_indices]
        np.add.at(point_count, (row_indices, column_indices), 1)
        np.maximum.at(max_observed_z, (row_indices, column_indices), points[:, 2])
        is_ground = classifications == LIDAR_GROUND_CLASS
        np.add.at(ground_count, (row_indices[is_ground], column_indices[is_ground]), 1)
        is_object = (~is_ground) & np.isfinite(heights) & (heights >= 0.0)
        object_rows = row_indices[is_object]
        object_columns = column_indices[is_object]
        object_heights = heights[is_object]
        if len(object_heights):
            min_seed = np.where(np.isnan(min_object_height), np.inf, min_object_height)
            np.minimum.at(min_seed, (object_rows, object_columns), object_heights)
            min_object_height[:] = np.where(np.isinf(min_seed), np.nan, min_seed)
            low_object = object_heights <= 5.0
            np.add.at(object_count_le5, (object_rows[low_object], object_columns[low_object]), 1)
        is_vegetation = np.asarray(
            [lidar_return_can_count_as_green(int(value)) for value in classifications],
            dtype=bool,
        ) & is_object
        if np.any(is_vegetation):
            vegetation_seed = np.where(np.isnan(vegetation_height), -np.inf, vegetation_height)
            np.maximum.at(vegetation_seed, (row_indices[is_vegetation], column_indices[is_vegetation]), heights[is_vegetation])
            vegetation_height[:] = np.where(np.isneginf(vegetation_seed), np.nan, vegetation_seed)

    surface_model = np.maximum(ground_model, np.where(np.isneginf(max_observed_z), ground_model, max_observed_z))
    return {
        "columns": columns,
        "rows": rows_count,
        "ground_height": ground_model,
        "surface_height": surface_model,
        "point_count": point_count,
        "ground_count": ground_count,
        "object_count_le5": object_count_le5,
        "min_object_height": min_object_height,
        "vegetation_height": vegetation_height,
    }


def height_gradient_rgb(values: Any) -> tuple[Any, float, float]:
    import numpy as np

    from .cli import viridis_rgb_array

    finite = values[np.isfinite(values)]
    if len(finite) == 0:
        z_min = 0.0
        z_max = 1.0
        normalized = np.zeros(values.shape, dtype=float)
    else:
        z_min = float(np.quantile(finite, 0.01))
        z_max = float(np.quantile(finite, 0.99))
        if z_max <= z_min:
            z_min = float(np.min(finite))
            z_max = float(np.max(finite))
        if z_max <= z_min:
            z_max = z_min + 1.0
        normalized = np.clip((values - z_min) / (z_max - z_min), 0.0, 1.0)
    flat = viridis_rgb_array(normalized.reshape(-1))
    return flat.reshape(values.shape[0], values.shape[1], 3), z_min, z_max


def slope_degrees(values: Any) -> Any:
    import numpy as np

    dy, dx = np.gradient(values, DIAGNOSTIC_CELL_SIZE_M, DIAGNOSTIC_CELL_SIZE_M)
    return np.degrees(np.arctan(np.hypot(dx, dy)))


def hillshade_rgb(values: Any) -> Any:
    import numpy as np

    dy, dx = np.gradient(values, DIAGNOSTIC_CELL_SIZE_M, DIAGNOSTIC_CELL_SIZE_M)
    azimuth = math.radians(315.0)
    altitude = math.radians(45.0)
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(dy, -dx)
    shaded = (
        np.sin(altitude) * np.cos(slope)
        + np.cos(altitude) * np.sin(slope) * np.cos(azimuth - aspect)
    )
    gray = np.rint(np.clip((shaded + 1.0) / 2.0, 0.0, 1.0) * 255).astype("uint8")
    return np.repeat(gray[:, :, None], 3, axis=2)


def grayscale_rgb(values: Any, *, min_value: float = 0.0, max_value: float | None = None, invert: bool = False) -> tuple[Any, float]:
    import numpy as np

    finite = values[np.isfinite(values)]
    if max_value is None:
        max_value = float(np.quantile(finite, 0.95)) if len(finite) else 1.0
    if max_value <= min_value:
        max_value = min_value + 1.0
    normalized = np.clip((values - min_value) / (max_value - min_value), 0.0, 1.0)
    if invert:
        normalized = 1.0 - normalized
    gray = np.rint(normalized * 255).astype("uint8")
    return np.repeat(gray[:, :, None], 3, axis=2), float(max_value)


def render_raster_png(
    rgb: Any,
    output_path: Path,
    *,
    transform: Any,
    dpi: int,
    title: str,
    legend: str,
    map_maker: str,
) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("LiDAR raster diagnostic rendering requires pillow and numpy") from exc

    from .cli import draw_pillow_text_fit, lidar_footer_text, pillow_layout_font

    metrics = diagnostic_plot_geometry(transform, dpi)
    px_per_mm = float(metrics["px_per_mm"])
    width = int(metrics["width"])
    height = int(metrics["height"])
    plot_left = int(metrics["plot_left"])
    plot_top = int(metrics["plot_top"])
    image = np.full((height, width, 4), 255, dtype=np.uint8)
    for row in range(rgb.shape[0]):
        for column in range(rgb.shape[1]):
            paint_cell(image, transform, metrics, column, row, rgb[row, column])
    draw_frame(image, metrics)
    pil_image = Image.fromarray(image, mode="RGBA")
    draw = ImageDraw.Draw(pil_image)
    title_font = pillow_layout_font(max(12, int(round(3.2 * px_per_mm))))
    footer_font = pillow_layout_font(max(10, int(round(2.6 * px_per_mm))))
    text_x = max(2, plot_left)
    draw_pillow_text_fit(draw, (text_x, max(2, plot_top - int(round(4.0 * px_per_mm)))), title, fill=(0, 0, 0, 255), font=title_font, max_width_px=width - text_x - 2)
    draw_pillow_text_fit(draw, (text_x, max(2, height - int(round(5.0 * px_per_mm)))), lidar_footer_text(transform, map_maker), fill=(0, 0, 0, 255), font=footer_font, max_width_px=width - text_x - 2)
    draw_pillow_text_fit(draw, (text_x, max(2, height - int(round(2.8 * px_per_mm)))), legend, fill=(0, 0, 0, 255), font=footer_font, max_width_px=width - text_x - 2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pil_image.save(output_path)
    return {
        "path": str(output_path),
        "bbox": [transform.min_x, transform.min_y, transform.max_x, transform.max_y],
        "paper_size": transform.paper_size,
        "scale": transform.scale,
        "width_px": width,
        "height_px": height,
        "plot_width_px": int(metrics["plot_width"]),
        "plot_height_px": int(metrics["plot_height"]),
        "dpi": dpi,
        "diagnostic_cell_size_m": DIAGNOSTIC_CELL_SIZE_M,
        "software": "mml-omap",
        "software_version": __version__,
    }


def render_contour_png(
    source_data: dict[str, Any],
    output_path: Path,
    *,
    transform: Any,
    dpi: int,
    map_title_text: str | None,
    map_maker: str,
) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("LiDAR contour diagnostic rendering requires pillow and numpy") from exc

    from .cli import (
        contour_features_from_xyz_grid,
        draw_pillow_text_fit,
        iter_geometry_parts,
        lidar_footer_text,
        map_title,
        pillow_layout_font,
    )

    metrics = diagnostic_plot_geometry(transform, dpi)
    px_per_mm = float(metrics["px_per_mm"])
    width = int(metrics["width"])
    height = int(metrics["height"])
    plot_left = int(metrics["plot_left"])
    plot_top = int(metrics["plot_top"])
    image = np.full((height, width, 4), 255, dtype=np.uint8)
    draw_frame(image, metrics)
    pil_image = Image.fromarray(image, mode="RGBA")
    draw = ImageDraw.Draw(pil_image)
    contour_features = contour_features_from_xyz_grid(
        source_data["xs"],
        source_data["ys"],
        source_data["elevation_points"],
        interval_m=1.0,
        index_contour_every=5,
    )

    def to_px(coordinate: Any) -> tuple[float, float]:
        x_mm, y_mm = transform.to_mm(coordinate)
        return x_mm * px_per_mm, y_mm * px_per_mm

    for feature in contour_features:
        symbol = feature["properties"].get("symbol")
        color = (210, 30, 30, 255) if symbol == "index_contour" else (60, 60, 60, 255)
        width_px = max(1, int(round((0.22 if symbol == "index_contour" else 0.12) * px_per_mm)))
        for part in iter_geometry_parts(feature.get("geometry") or {}):
            if part["type"] != "LineString":
                continue
            points = [to_px(point) for point in part.get("coordinates") or []]
            if len(points) >= 2:
                draw.line(points, fill=color, width=width_px)
    title_font = pillow_layout_font(max(12, int(round(3.2 * px_per_mm))))
    footer_font = pillow_layout_font(max(10, int(round(2.6 * px_per_mm))))
    text_x = max(2, plot_left)
    title = f"{map_title(output_path, map_title_text)} LiDAR 1 m contours"
    legend = f"1 m interval, red every 5 m | {len(contour_features)} contour features"
    draw_pillow_text_fit(draw, (text_x, max(2, plot_top - int(round(4.0 * px_per_mm)))), title, fill=(0, 0, 0, 255), font=title_font, max_width_px=width - text_x - 2)
    draw_pillow_text_fit(draw, (text_x, max(2, height - int(round(5.0 * px_per_mm)))), lidar_footer_text(transform, map_maker), fill=(0, 0, 0, 255), font=footer_font, max_width_px=width - text_x - 2)
    draw_pillow_text_fit(draw, (text_x, max(2, height - int(round(2.8 * px_per_mm)))), legend, fill=(0, 0, 0, 255), font=footer_font, max_width_px=width - text_x - 2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pil_image.save(output_path)
    return {
        "path": str(output_path),
        "contour_interval_m": 1.0,
        "index_contour_every_m": 5.0,
        "feature_count": len(contour_features),
        "software": "mml-omap",
        "software_version": __version__,
    }


def render_laserscan_diagnostic_pngs(source_data: dict[str, Any], output_base: Path, args: Any, transform: Any) -> dict[str, Any]:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("LaserScan-style diagnostics require numpy") from exc

    from .cli import map_title, progress

    surfaces = diagnostic_surfaces(source_data, transform)
    reports: dict[str, Any] = {}
    stem = output_base.name

    reports["contours_1m"] = render_contour_png(
        source_data,
        output_base.with_name(stem + "-lidar-contours-1m").with_suffix(".png"),
        transform=transform,
        dpi=args.dpi,
        map_title_text=args.map_title,
        map_maker=args.map_maker,
    )

    for prefix, values in (("ground", surfaces["ground_height"]), ("surface", surfaces["surface_height"])):
        gradient, z_min, z_max = height_gradient_rgb(values)
        reports[f"{prefix}_gradient"] = render_raster_png(
            gradient,
            output_base.with_name(f"{stem}-lidar-{prefix}-gradient").with_suffix(".png"),
            transform=transform,
            dpi=args.dpi,
            title=f"{map_title(output_base, args.map_title)} LiDAR {prefix} height gradient",
            legend=f"Height gradient {z_min:.1f}-{z_max:.1f} m, 1 m cells",
            map_maker=args.map_maker,
        )
        reports[f"{prefix}_shading"] = render_raster_png(
            hillshade_rgb(values),
            output_base.with_name(f"{stem}-lidar-{prefix}-shading").with_suffix(".png"),
            transform=transform,
            dpi=args.dpi,
            title=f"{map_title(output_base, args.map_title)} LiDAR {prefix} hillshade",
            legend="Analytical hillshade from 1 m diagnostic surface",
            map_maker=args.map_maker,
        )
        slope_rgb, slope_max = grayscale_rgb(slope_degrees(values), min_value=0.0, max_value=45.0, invert=True)
        reports[f"{prefix}_slope"] = render_raster_png(
            slope_rgb,
            output_base.with_name(f"{stem}-lidar-{prefix}-slope").with_suffix(".png"),
            transform=transform,
            dpi=args.dpi,
            title=f"{map_title(output_base, args.map_title)} LiDAR {prefix} slope",
            legend=f"Slope 0-{slope_max:.0f} degrees, darker is steeper",
            map_maker=args.map_maker,
        )

    coverage_rgb = np.zeros((*surfaces["point_count"].shape, 3), dtype=np.uint8)
    coverage_rgb[surfaces["point_count"] == 0] = (0, 120, 220)
    coverage_rgb[(surfaces["point_count"] > 0) & (surfaces["ground_count"] == 0)] = (245, 215, 45)
    coverage_rgb[surfaces["ground_count"] > 0] = (0, 0, 0)
    reports["ground_coverage"] = render_raster_png(
        coverage_rgb,
        output_base.with_name(stem + "-lidar-ground-coverage").with_suffix(".png"),
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR ground coverage",
        legend="Blue: no point | yellow: no ground return | black: ground return present",
        map_maker=args.map_maker,
    )

    min_object_rgb, min_object_max = grayscale_rgb(np.nan_to_num(surfaces["min_object_height"], nan=0.0), min_value=0.0, max_value=5.0)
    min_object_rgb[np.isnan(surfaces["min_object_height"])] = (210, 30, 30)
    reports["minimum_object_height"] = render_raster_png(
        min_object_rgb,
        output_base.with_name(stem + "-lidar-minimum-object-height").with_suffix(".png"),
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR minimum object height",
        legend=f"Red: no object point | black-white: lowest object height 0-{min_object_max:.0f} m",
        map_maker=args.map_maker,
    )

    point_count_rgb, count_max = grayscale_rgb(surfaces["object_count_le5"].astype(float), min_value=0.0)
    reports["point_count_up_to_5m"] = render_raster_png(
        point_count_rgb,
        output_base.with_name(stem + "-lidar-point-count-up-to-5m").with_suffix(".png"),
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR object point count",
        legend=f"Object points up to 5 m above ground, white at >= {count_max:.0f} points/cell",
        map_maker=args.map_maker,
    )

    vegetation_rgb, vegetation_max = grayscale_rgb(np.nan_to_num(surfaces["vegetation_height"], nan=0.0), min_value=0.0)
    vegetation_rgb[np.isnan(surfaces["vegetation_height"])] = (255, 255, 255)
    reports["vegetation_height"] = render_raster_png(
        vegetation_rgb,
        output_base.with_name(stem + "-lidar-vegetation-height").with_suffix(".png"),
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR vegetation height",
        legend=f"Vegetation return height above ground, white at >= {vegetation_max:.1f} m",
        map_maker=args.map_maker,
    )
    progress(f"Wrote {len(reports)} LaserScan-style LiDAR diagnostic PNGs.")
    return reports
