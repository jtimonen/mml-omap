"""LiDAR raster support-layer rendering."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from . import __version__


LIDAR_RASTER_CELL_SIZE_M = 1.0


def raster_plot_geometry(transform: Any, dpi: int) -> dict[str, int | float]:
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


def raster_grid_shape(transform: Any) -> tuple[int, int]:
    columns = max(1, int(math.ceil(transform.width_m / LIDAR_RASTER_CELL_SIZE_M)))
    rows_count = max(1, int(math.ceil(transform.height_m / LIDAR_RASTER_CELL_SIZE_M)))
    return columns, rows_count


def raster_cell_bounds(transform: Any, column: int, row: int) -> tuple[float, float, float, float]:
    x0 = transform.min_x + column * LIDAR_RASTER_CELL_SIZE_M
    y0 = transform.min_y + row * LIDAR_RASTER_CELL_SIZE_M
    return (
        x0,
        y0,
        min(x0 + LIDAR_RASTER_CELL_SIZE_M, transform.max_x),
        min(y0 + LIDAR_RASTER_CELL_SIZE_M, transform.max_y),
    )


def cell_pixel_polygon(transform: Any, metrics: dict[str, int | float], column: int, row: int) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = raster_cell_bounds(transform, column, row)
    px_per_mm = float(metrics["px_per_mm"])
    return [
        tuple(value * px_per_mm for value in transform.to_mm(point))
        for point in ([x0, y0], [x1, y0], [x1, y1], [x0, y1])
    ]


def draw_frame(image: Any, metrics: dict[str, int | float]) -> None:
    plot_left = int(metrics["plot_left"])
    plot_top = int(metrics["plot_top"])
    plot_right = int(metrics["plot_right"])
    plot_bottom = int(metrics["plot_bottom"])
    image[plot_top, plot_left:plot_right, 0:3] = 0
    image[plot_bottom - 1, plot_left:plot_right, 0:3] = 0
    image[plot_top:plot_bottom, plot_left, 0:3] = 0
    image[plot_top:plot_bottom, plot_right - 1, 0:3] = 0


def raster_to_page_image(rgb: Any, transform: Any, metrics: dict[str, int | float]) -> Any:
    from PIL import Image
    import numpy as np

    source = Image.fromarray(np.flipud(rgb).astype("uint8"), mode="RGB")
    page = Image.new("RGBA", (int(metrics["width"]), int(metrics["height"])), (255, 255, 255, 255))
    resized = source.resize((int(metrics["plot_width"]), int(metrics["plot_height"])), resample=Image.Resampling.NEAREST)
    page.paste(resized.convert("RGBA"), (int(metrics["plot_left"]), int(metrics["plot_top"])))
    return page


def points_to_raster_coordinates(points: Any, transform: Any) -> tuple[Any, Any, Any]:
    import numpy as np

    dx = points[:, 0] - transform.center_x
    dy = points[:, 1] - transform.center_y
    map_x = transform._cos_declination * dx - transform._sin_declination * dy + transform.width_m / 2.0
    map_y = transform._sin_declination * dx + transform._cos_declination * dy + transform.height_m / 2.0
    mask = (
        np.isfinite(points[:, 0])
        & np.isfinite(points[:, 1])
        & (map_x >= 0.0)
        & (map_x <= transform.width_m)
        & (map_y >= 0.0)
        & (map_y <= transform.height_m)
    )
    return map_x, map_y, mask


def raster_cell_centers_world(transform: Any, columns: int, rows_count: int) -> tuple[Any, Any]:
    import numpy as np

    center_map_x = (np.arange(columns, dtype=float) + 0.5) * LIDAR_RASTER_CELL_SIZE_M - transform.width_m / 2.0
    center_map_y = (np.arange(rows_count, dtype=float) + 0.5) * LIDAR_RASTER_CELL_SIZE_M - transform.height_m / 2.0
    map_x_grid, map_y_grid = np.meshgrid(center_map_x, center_map_y)
    world_x = transform.center_x + transform._cos_declination * map_x_grid + transform._sin_declination * map_y_grid
    world_y = transform.center_y - transform._sin_declination * map_x_grid + transform._cos_declination * map_y_grid
    return world_x, world_y


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

    center_x_grid, center_y_grid = raster_cell_centers_world(transform, columns, rows_count)
    center_x = center_x_grid.reshape(-1)
    center_y = center_y_grid.reshape(-1)
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
    return grid[y_indexes, x_indexes].reshape(rows_count, columns)


def raster_surfaces(source_data: dict[str, Any], transform: Any) -> dict[str, Any]:
    import numpy as np

    from .lidar import LIDAR_GROUND_CLASS, lidar_return_can_count_as_green, normalize_lidar_classification

    columns, rows_count = raster_grid_shape(transform)
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
    vegetation_height = np.full((rows_count, columns), np.nan, dtype=float)
    max_observed_z = np.full((rows_count, columns), -np.inf, dtype=float)

    rows_array = np.asarray(source_data["lidar_rows"], dtype=float)
    if rows_array.ndim != 2 or rows_array.shape[1] < 4:
        raise ValueError("Point rows must contain x, y, z, classification")
    map_x, map_y, frame_mask = points_to_raster_coordinates(rows_array, transform)
    mask = frame_mask & np.isfinite(rows_array[:, 2])
    points = rows_array[mask]
    if len(points):
        point_map_x = map_x[mask]
        point_map_y = map_y[mask]
        column_indices = np.clip(
            np.floor(point_map_x / LIDAR_RASTER_CELL_SIZE_M).astype(int),
            0,
            columns - 1,
        )
        row_indices = np.clip(
            np.floor(point_map_y / LIDAR_RASTER_CELL_SIZE_M).astype(int),
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
        "vegetation_height": vegetation_height,
    }


def median_height_surface(source_data: dict[str, Any], transform: Any) -> tuple[Any, int, int]:
    import numpy as np

    columns, rows_count = raster_grid_shape(transform)
    rows_array = np.asarray(source_data["lidar_rows"], dtype=float)
    values = np.full((rows_count, columns), np.nan, dtype=float)
    map_x, map_y, frame_mask = points_to_raster_coordinates(rows_array, transform)
    mask = frame_mask & np.isfinite(rows_array[:, 2])
    points = rows_array[mask]
    if len(points) == 0:
        return values, 0, 0
    point_map_x = map_x[mask]
    point_map_y = map_y[mask]
    column_indices = np.clip(
        np.floor(point_map_x / LIDAR_RASTER_CELL_SIZE_M).astype(int),
        0,
        columns - 1,
    )
    row_indices = np.clip(
        np.floor(point_map_y / LIDAR_RASTER_CELL_SIZE_M).astype(int),
        0,
        rows_count - 1,
    )
    flat_cells = row_indices * columns + column_indices
    order = np.argsort(flat_cells)
    sorted_cells = flat_cells[order]
    sorted_z = points[:, 2][order]
    unique_cells, first_indexes, counts = np.unique(sorted_cells, return_index=True, return_counts=True)
    for flat_cell, start, count in zip(unique_cells, first_indexes, counts):
        row = int(flat_cell // columns)
        column = int(flat_cell % columns)
        values[row, column] = float(np.median(sorted_z[start : start + count]))
    return values, int(len(unique_cells)), int(len(points))


def return_type_surface(source_data: dict[str, Any], transform: Any) -> tuple[Any, dict[str, int], dict[str, int]]:
    import numpy as np

    from .lidar import LIDAR_RETURN_TYPE_STYLES, lidar_return_type

    columns, rows_count = raster_grid_shape(transform)
    keys = list(LIDAR_RETURN_TYPE_STYLES)
    key_index = {key: index for index, key in enumerate(keys)}
    counts_by_cell = np.zeros((rows_count, columns, len(keys)), dtype=np.uint16)
    point_counts = {key: 0 for key in keys}
    rows_array = np.asarray(source_data["lidar_rows"], dtype=float)
    map_x, map_y, frame_mask = points_to_raster_coordinates(rows_array, transform)
    mask = frame_mask
    points = rows_array[mask]
    point_map_x = map_x[mask]
    point_map_y = map_y[mask]
    for x_raster, y_raster, (_x_raw, _y_raw, _z_raw, classification) in zip(point_map_x, point_map_y, points):
        column = int(np.clip(math.floor(x_raster / LIDAR_RASTER_CELL_SIZE_M), 0, columns - 1))
        row = int(np.clip(math.floor(y_raster / LIDAR_RASTER_CELL_SIZE_M), 0, rows_count - 1))
        return_type = lidar_return_type(int(classification) if np.isfinite(classification) else None)
        counts_by_cell[row, column, key_index[return_type]] += 1
        point_counts[return_type] += 1
    dominant = np.argmax(counts_by_cell, axis=2)
    totals = np.sum(counts_by_cell, axis=2)
    cell_type_counts = {key: 0 for key in keys}
    rgb = np.full((rows_count, columns, 3), 255, dtype=np.uint8)
    for key, index in key_index.items():
        mask_cells = (totals > 0) & (dominant == index)
        cell_type_counts[key] = int(np.count_nonzero(mask_cells))
        rgb[mask_cells] = LIDAR_RETURN_TYPE_STYLES[key][1]
    return rgb, point_counts, cell_type_counts


def elevation_color_ramp_rgb(values: Any) -> tuple[Any, float, float]:
    import numpy as np

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
    normalized = np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0)
    flat = viridis_rgb_array(normalized.reshape(-1))
    return flat.reshape(values.shape[0], values.shape[1], 3), z_min, z_max


def viridis_rgb_array(values: Any) -> Any:
    import numpy as np

    anchors = np.asarray(
        [
            [68, 1, 84],
            [71, 44, 122],
            [59, 81, 139],
            [44, 113, 142],
            [33, 144, 141],
            [39, 173, 129],
            [92, 200, 99],
            [170, 220, 50],
            [253, 231, 37],
        ],
        dtype=float,
    )
    values = np.clip(values, 0.0, 1.0)
    scaled = values * (len(anchors) - 1)
    lower = np.floor(scaled).astype(int)
    upper = np.clip(lower + 1, 0, len(anchors) - 1)
    t = (scaled - lower)[:, None]
    return np.rint(anchors[lower] + (anchors[upper] - anchors[lower]) * t).astype(np.uint8)


def slope_degrees(values: Any) -> Any:
    import numpy as np

    dy, dx = np.gradient(values, LIDAR_RASTER_CELL_SIZE_M, LIDAR_RASTER_CELL_SIZE_M)
    return np.degrees(np.arctan(np.hypot(dx, dy)))


def hillshade_rgb(values: Any) -> Any:
    import numpy as np

    dy, dx = np.gradient(values, LIDAR_RASTER_CELL_SIZE_M, LIDAR_RASTER_CELL_SIZE_M)
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


def green_height_rgb(values: Any, *, min_value: float = 0.0, max_value: float | None = None) -> tuple[Any, float]:
    import numpy as np

    finite = values[np.isfinite(values)]
    if max_value is None:
        max_value = float(np.quantile(finite, 0.95)) if len(finite) else 1.0
    if max_value <= min_value:
        max_value = min_value + 1.0
    normalized = np.clip((values - min_value) / (max_value - min_value), 0.0, 1.0)
    normalized = np.nan_to_num(normalized, nan=0.0, posinf=1.0, neginf=0.0)
    low = np.asarray([245, 250, 240], dtype=float)
    high = np.asarray([0, 112, 60], dtype=float)
    return np.rint(low + (high - low) * normalized[:, :, None]).astype("uint8"), float(max_value)


def viridis_scale(z_min: float, z_max: float) -> tuple[list[tuple[int, int, int]], str, str]:
    colors = viridis_rgb_array(__import__("numpy").linspace(0.0, 1.0, 9))
    return [tuple(int(value) for value in color) for color in colors], f"{z_min:.1f}", f"{z_max:.1f}"


def grayscale_scale(low_label: str, high_label: str, *, invert: bool = False) -> tuple[list[tuple[int, int, int]], str, str]:
    low = (255, 255, 255) if invert else (0, 0, 0)
    high = (0, 0, 0) if invert else (255, 255, 255)
    return [low, high], low_label, high_label


def green_scale(low_label: str, high_label: str) -> tuple[list[tuple[int, int, int]], str, str]:
    return [(245, 250, 240), (0, 112, 60)], low_label, high_label


def render_raster_png(
    rgb: Any,
    output_path: Path,
    *,
    transform: Any,
    dpi: int,
    title: str,
    legend: str,
    map_maker: str,
    color_scale: tuple[list[tuple[int, int, int]], str, str] | None = None,
    swatches: list[tuple[tuple[int, int, int], str]] | None = None,
) -> dict[str, Any]:
    try:
        from PIL import Image, ImageDraw
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("LiDAR raster rendering requires pillow and numpy") from exc

    from .cli import draw_pillow_text_fit, lidar_footer_text, pillow_layout_font

    metrics = raster_plot_geometry(transform, dpi)
    px_per_mm = float(metrics["px_per_mm"])
    width = int(metrics["width"])
    height = int(metrics["height"])
    plot_left = int(metrics["plot_left"])
    plot_top = int(metrics["plot_top"])
    pil_image = raster_to_page_image(rgb, transform, metrics)
    image = np.asarray(pil_image, dtype=np.uint8).copy()
    draw_frame(image, metrics)
    pil_image = Image.fromarray(image, mode="RGBA")
    draw = ImageDraw.Draw(pil_image)
    title_font = pillow_layout_font(max(12, int(round(3.2 * px_per_mm))))
    footer_font = pillow_layout_font(max(10, int(round(2.6 * px_per_mm))))
    text_x = max(2, plot_left)
    draw_pillow_text_fit(draw, (text_x, max(2, plot_top - int(round(4.0 * px_per_mm)))), title, fill=(0, 0, 0, 255), font=title_font, max_width_px=width - text_x - 2)
    draw_pillow_text_fit(draw, (text_x, max(2, height - int(round(5.0 * px_per_mm)))), lidar_footer_text(transform, map_maker), fill=(0, 0, 0, 255), font=footer_font, max_width_px=width - text_x - 2)
    draw_pillow_text_fit(draw, (text_x, max(2, height - int(round(2.8 * px_per_mm)))), legend, fill=(0, 0, 0, 255), font=footer_font, max_width_px=width - text_x - 2)
    if color_scale is not None:
        scale_colors, low_label, high_label = color_scale
        bar_width = max(8, int(round(2.5 * px_per_mm)))
        bar_height = min(int(metrics["plot_height"]), max(24, int(round(35.0 * px_per_mm))))
        bar_x0 = min(width - bar_width - 2, int(metrics["plot_right"]) + int(round(2.5 * px_per_mm)))
        if bar_x0 + bar_width + int(round(12.0 * px_per_mm)) > width:
            bar_x0 = max(2, int(metrics["plot_right"]) - bar_width - int(round(2.0 * px_per_mm)))
        bar_y0 = int(metrics["plot_top"]) + int(round(2.0 * px_per_mm))
        for offset in range(bar_height):
            t = 1.0 - offset / max(bar_height - 1, 1)
            scaled = t * (len(scale_colors) - 1)
            lower = max(0, min(int(math.floor(scaled)), len(scale_colors) - 1))
            upper = max(0, min(lower + 1, len(scale_colors) - 1))
            local_t = scaled - lower
            low_color = scale_colors[lower]
            high_color = scale_colors[upper]
            color = tuple(
                int(round(low_color[channel] * (1.0 - local_t) + high_color[channel] * local_t))
                for channel in range(3)
            )
            draw.line([(bar_x0, bar_y0 + offset), (bar_x0 + bar_width, bar_y0 + offset)], fill=(*color, 255))
        draw.rectangle((bar_x0, bar_y0, bar_x0 + bar_width, bar_y0 + bar_height - 1), outline=(0, 0, 0, 255))
        label_x = min(width - int(round(20.0 * px_per_mm)), bar_x0 + bar_width + int(round(1.4 * px_per_mm)))
        draw.text((label_x, bar_y0), high_label, fill=(0, 0, 0, 255), font=footer_font)
        draw.text((label_x, bar_y0 + bar_height - max(10, int(round(2.6 * px_per_mm)))), low_label, fill=(0, 0, 0, 255), font=footer_font)
        if swatches:
            swatch_size = max(8, int(round(2.5 * px_per_mm)))
            y = bar_y0 + bar_height + int(round(2.0 * px_per_mm))
            for color, label in swatches:
                if y + swatch_size >= height:
                    break
                draw.rectangle((bar_x0, y, bar_x0 + swatch_size, y + swatch_size), fill=(*color, 255), outline=(0, 0, 0, 255))
                draw.text((bar_x0 + swatch_size + 5, y), label, fill=(0, 0, 0, 255), font=footer_font)
                y += swatch_size + int(round(1.5 * px_per_mm))
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
        "LIDAR_RASTER_CELL_SIZE_M": LIDAR_RASTER_CELL_SIZE_M,
        "software": "mml-omap",
        "software_version": __version__,
    }


def render_lidar_raster_pngs(source_data: dict[str, Any], output_base: Path, args: Any, transform: Any) -> dict[str, Any]:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("LiDAR raster support layers require numpy") from exc

    from .cli import map_title, progress

    raster_dir = output_base.parent / "lidar-rasters"
    raster_stem = output_base.name
    progress("Preparing LiDAR raster support layers from ground grid and point cloud...")
    surfaces = raster_surfaces(source_data, transform)
    reports: dict[str, Any] = {}

    progress("Rendering LiDAR raster: median point height...")
    median_height, occupied_cells, point_count = median_height_surface(source_data, transform)
    height_rgb, z_min, z_max = elevation_color_ramp_rgb(median_height)
    reports["median_point_height"] = render_raster_png(
        height_rgb,
        raster_dir / f"{raster_stem}-median-point-height.png",
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR median point height",
        legend=f"Median raw z/elevation of returns inside each 1 m x 1 m cell: {z_min:.1f}-{z_max:.1f} m | {occupied_cells} cells | {point_count} points",
        map_maker=args.map_maker,
        color_scale=viridis_scale(z_min, z_max),
    )

    progress("Rendering LiDAR raster: dominant return type...")
    return_type_rgb, return_type_point_counts, return_type_cell_counts = return_type_surface(source_data, transform)
    return_type_report = render_raster_png(
        return_type_rgb,
        raster_dir / f"{raster_stem}-return-types.png",
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR return types",
        legend="Dominant LAS return class group per 1 m cell",
        map_maker=args.map_maker,
    )
    return_type_report.update(
        {
            "cell_value": "dominant_las_return_type",
            "return_type_point_counts": return_type_point_counts,
            "return_type_cell_counts": return_type_cell_counts,
        }
    )
    reports["return_types"] = return_type_report

    for prefix, values in (("ground", surfaces["ground_height"]), ("surface", surfaces["surface_height"])):
        progress(f"Rendering LiDAR rasters: {prefix} elevation, hillshade, and slope...")
        elevation_rgb, z_min, z_max = elevation_color_ramp_rgb(values)
        reports[f"{prefix}_elevation"] = render_raster_png(
            elevation_rgb,
            raster_dir / f"{raster_stem}-{prefix}-elevation.png",
            transform=transform,
            dpi=args.dpi,
            title=f"{map_title(output_base, args.map_title)} LiDAR {prefix} elevation",
            legend=f"{prefix.title()} elevation {z_min:.1f}-{z_max:.1f} m, 1 m cells",
            map_maker=args.map_maker,
            color_scale=viridis_scale(z_min, z_max),
        )
        reports[f"{prefix}_shading"] = render_raster_png(
            hillshade_rgb(values),
            raster_dir / f"{raster_stem}-{prefix}-shading.png",
            transform=transform,
            dpi=args.dpi,
            title=f"{map_title(output_base, args.map_title)} LiDAR {prefix} hillshade",
            legend="Analytical hillshade from 1 m raster surface",
            map_maker=args.map_maker,
        )
        slope_rgb, slope_max = grayscale_rgb(slope_degrees(values), min_value=0.0, max_value=45.0, invert=True)
        reports[f"{prefix}_slope"] = render_raster_png(
            slope_rgb,
            raster_dir / f"{raster_stem}-{prefix}-slope.png",
            transform=transform,
            dpi=args.dpi,
            title=f"{map_title(output_base, args.map_title)} LiDAR {prefix} slope",
            legend=f"Slope 0-{slope_max:.0f} degrees, darker is steeper",
            map_maker=args.map_maker,
            color_scale=grayscale_scale("0 deg", f"{slope_max:.0f} deg", invert=True),
        )

    progress("Rendering LiDAR raster: ground coverage...")
    coverage_rgb = np.zeros((*surfaces["point_count"].shape, 3), dtype=np.uint8)
    coverage_rgb[surfaces["point_count"] == 0] = (0, 120, 220)
    coverage_rgb[(surfaces["point_count"] > 0) & (surfaces["ground_count"] == 0)] = (245, 215, 45)
    coverage_rgb[surfaces["ground_count"] > 0] = (0, 0, 0)
    reports["ground_coverage"] = render_raster_png(
        coverage_rgb,
        raster_dir / f"{raster_stem}-ground-coverage.png",
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR ground coverage",
        legend="Blue: no point | yellow: no ground return | black: ground return present",
        map_maker=args.map_maker,
    )

    progress("Rendering LiDAR raster: object point count up to 5 m...")
    point_count_rgb, count_max = grayscale_rgb(surfaces["object_count_le5"].astype(float), min_value=0.0)
    reports["point_count_up_to_5m"] = render_raster_png(
        point_count_rgb,
        raster_dir / f"{raster_stem}-point-count-up-to-5m.png",
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR object point count",
        legend=f"Object points up to 5 m above ground, white at >= {count_max:.0f} points/cell",
        map_maker=args.map_maker,
        color_scale=grayscale_scale("0", f"{count_max:.0f}"),
    )

    progress("Rendering LiDAR raster: vegetation height...")
    vegetation_rgb, vegetation_max = green_height_rgb(np.nan_to_num(surfaces["vegetation_height"], nan=0.0), min_value=0.0)
    vegetation_rgb[np.isnan(surfaces["vegetation_height"])] = (255, 255, 255)
    reports["vegetation_height"] = render_raster_png(
        vegetation_rgb,
        raster_dir / f"{raster_stem}-vegetation-height.png",
        transform=transform,
        dpi=args.dpi,
        title=f"{map_title(output_base, args.map_title)} LiDAR vegetation height",
        legend=f"Vegetation return height above ground, pale-to-green ramp up to {vegetation_max:.1f} m",
        map_maker=args.map_maker,
        color_scale=green_scale("0 m", f"{vegetation_max:.1f} m"),
        swatches=[((255, 255, 255), "No vegetation return")],
    )
    progress(f"Wrote {len(reports)} LiDAR raster PNGs to {raster_dir}.")
    return reports
