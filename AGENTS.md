- Read the ISOM specification in `reference/` and
  https://omapwiki.orienteering.sport/specifications/isom/ before changing
  symbol dimensions, symbol mappings, rendering order, or map-generation
  algorithms.
- Keep documentation connected to code changes:
  - CLI workflow, output paths, examples, dependencies, or user-facing command
    changes must update `README.md`.
  - Released or committed behavior changes must update `CHANGELOG.md`.
  - MML table, `kohdeluokka`, internal object type, or ISOM symbol mapping
    changes must update `docs/mml-to-isom-mapping.md`.
  - Build pipeline, contour, cliff, elevation, vegetation, LiDAR, point-cloud,
    or orienteering map style changes must update `docs/pipeline.md`.
  - Built-in symbol-library or renderer-symbol contract changes must update
    `docs/isom-symbol-library.md`.
- Version committed or released code changes:
  - `pyproject.toml` `project.version` is the source of truth.
  - `src/mml_omap/__init__.py` must read the installed package metadata version,
    not hardcode a separate version string.
  - Before committing or releasing behavior, CLI, output, dependency, or public
    API changes, bump `pyproject.toml` and update `CHANGELOG.md`.
  - Whenever a new version is made, add or update the matching section in
    `CHANGELOG.md`.
- Do not keep backwards-compatibility shims, duplicate old workflows, fallback
  code paths, or dead code unless the user explicitly asks for compatibility.
  Prefer one current path that uses the best available combined data sources,
  and remove or update stale code and docs in the same change.
- Never preserve legacy behavior, compatibility aliases, old output formats,
  duplicate APIs, or transitional wrappers by default. Replace them with the
  current implementation and delete the stale path in the same change.
