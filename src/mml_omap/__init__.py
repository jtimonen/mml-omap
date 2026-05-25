"""Tools for generating orienteering-oriented maps from MML open data."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def package_version() -> str:
    try:
        return version("mml-omap")
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject_path.exists():
            for line in pyproject_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("version"):
                    _key, value = stripped.split("=", 1)
                    return value.strip().strip('"')
        return "0+unknown"


__version__ = package_version()
