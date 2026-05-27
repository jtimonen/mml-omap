"""LiDAR classification constants and helpers."""

from __future__ import annotations


DEFAULT_GREEN_GROUND_HEIGHT_M = 0.8
DEFAULT_GREEN_MAX_HEIGHT_M = 5.0
DEFAULT_GREEN_SLOW_RATIO = 0.68
DEFAULT_GREEN_FIGHT_RATIO = 1.13
DEFAULT_GREEN_MIN_HITS = 8
DEFAULT_GREEN_FIGHT_MIN_HITS = 24
DEFAULT_GREEN_MIN_REGION_AREA_M2 = 200.0

LIDAR_GROUND_CLASS = 2
LIDAR_VEGETATION_CLASSES = {3, 4, 5}
LIDAR_UNCLASSIFIED_VEGETATION_CANDIDATE_CLASSES = {0, 1}
LIDAR_VEGETATION_EXCLUDED_CLASSES = {6, 7, 9, 17, 18}

LIDAR_RETURN_TYPE_STYLES: dict[str, tuple[str, tuple[int, int, int]]] = {
    "ground": ("Ground", (166, 118, 64)),
    "water": ("Water", (0, 143, 213)),
    "low_vegetation": ("Low vegetation", (196, 230, 126)),
    "medium_vegetation": ("Medium vegetation", (91, 184, 76)),
    "high_vegetation": ("High vegetation", (0, 112, 60)),
    "building": ("Building", (60, 60, 60)),
    "noise": ("Noise", (180, 0, 180)),
    "other": ("Other", (150, 150, 150)),
}


def normalize_lidar_classification(classification: int | None) -> int | None:
    if classification is None:
        return None
    return int(classification) & 31


def lidar_return_type(classification: int | None) -> str:
    normalized = normalize_lidar_classification(classification)
    if normalized == LIDAR_GROUND_CLASS:
        return "ground"
    if normalized == 9:
        return "water"
    if normalized == 3:
        return "low_vegetation"
    if normalized == 4:
        return "medium_vegetation"
    if normalized == 5:
        return "high_vegetation"
    if normalized == 6:
        return "building"
    if normalized in {7, 18}:
        return "noise"
    return "other"


def lidar_return_can_support_vegetation(classification: int | None) -> bool:
    if classification in LIDAR_VEGETATION_EXCLUDED_CLASSES:
        return False
    return classification == LIDAR_GROUND_CLASS or lidar_return_can_count_as_green(classification)


def lidar_return_can_count_as_green(classification: int | None) -> bool:
    if classification in LIDAR_VEGETATION_CLASSES:
        return True
    if classification is None:
        return True
    return classification in LIDAR_UNCLASSIFIED_VEGETATION_CANDIDATE_CLASSES
