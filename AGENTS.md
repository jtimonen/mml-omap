- Read the ISOM specification in `references/` and
  https://omapwiki.orienteering.sport/specifications/isom/ before changing
  symbol dimensions, symbol mappings, rendering order, or map-generation
  algorithms.
- Keep documentation connected to code changes:
  - CLI workflow, output paths, examples, dependencies, or user-facing command
    changes must update `README.md`.
  - MML table, `kohdeluokka`, internal object type, or ISOM symbol mapping
    changes must update `docs/mml-to-isom-mapping.md`.
  - Build pipeline, contour, cliff, elevation, vegetation, LiDAR, point-cloud,
    or orienteering map style changes must update `docs/pipeline.md`.
  - Built-in symbol-library or renderer-symbol contract changes must update
    `docs/isom-symbol-library.md`.
- Version committed or released code changes:
  - Keep `pyproject.toml` `project.version` and
    `src/mml_omap/__init__.py` `__version__` identical.
  - Before committing or releasing behavior, CLI, output, dependency, or public
    API changes, bump both version declarations in the same change set.
- Do not keep backwards-compatibility shims, duplicate old workflows, fallback
  code paths, or dead code unless the user explicitly asks for compatibility.
  Prefer one current path that uses the best available combined data sources,
  and remove or update stale code and docs in the same change.
