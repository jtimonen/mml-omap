"""Continuous terrain model helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GroundModel:
    """Smoothed ground surface with continuous interpolation over a regular grid."""

    xs: Any
    ys: Any
    grid: Any

    def __post_init__(self) -> None:
        import numpy as np

        object.__setattr__(self, "xs", np.asarray(self.xs, dtype=float))
        object.__setattr__(self, "ys", np.asarray(self.ys, dtype=float))
        object.__setattr__(self, "grid", np.asarray(self.grid, dtype=float))
        if self.grid.shape != (len(self.ys), len(self.xs)):
            raise ValueError("GroundModel grid shape must be (len(ys), len(xs))")

    @classmethod
    def from_points(cls, xs: list[float], ys: list[float], points: dict[tuple[float, float], float]) -> "GroundModel":
        import numpy as np

        grid = np.asarray([[points[(x, y)] for x in xs] for y in ys], dtype=float)
        return cls(xs, ys, grid)

    def evaluate(self, x: Any, y: Any) -> Any:
        """Evaluate ground elevation at arbitrary x/y coordinates."""

        return self._spline().ev(y, x)

    def gradient(self, x: Any, y: Any) -> tuple[Any, Any]:
        """Evaluate dz/dx and dz/dy at arbitrary x/y coordinates."""

        spline = self._spline()
        dz_dx = spline.ev(y, x, dx=0, dy=1)
        dz_dy = spline.ev(y, x, dx=1, dy=0)
        return dz_dx, dz_dy

    def sample(self, xs: Any | None = None, ys: Any | None = None) -> Any:
        """Sample the continuous model on a regular x/y grid."""

        import numpy as np

        sample_xs = self.xs if xs is None else np.asarray(xs, dtype=float)
        sample_ys = self.ys if ys is None else np.asarray(ys, dtype=float)
        x_grid, y_grid = np.meshgrid(sample_xs, sample_ys)
        return self.evaluate(x_grid, y_grid)

    def _spline(self) -> Any:
        cached = getattr(self, "_cached_spline", None)
        if cached is not None:
            return cached
        try:
            from scipy.interpolate import RectBivariateSpline
        except ImportError as exc:
            raise RuntimeError("Continuous ground model evaluation requires scipy") from exc
        kx = min(3, max(1, len(self.ys) - 1))
        ky = min(3, max(1, len(self.xs) - 1))
        spline = RectBivariateSpline(self.ys, self.xs, self.grid, kx=kx, ky=ky, s=0)
        object.__setattr__(self, "_cached_spline", spline)
        return spline
