# Releasing

Maintainer notes for versioning and cutting releases.

## Version line

- SemVer. MAJOR breaks existing installs (reflash or manual migration), MINOR adds features, PATCH fixes bugs.
- Stabilization goes through `v1.0.0-beta.N` pre-releases when feature-complete, `v1.0.0` when field-proven.
- Between releases `__version__` in `camlab/__init__.py` carries the next version with `-dev` (for example `1.0.0-beta-dev`). The GUI status strip shows it, so test builds identify themselves. `-dev` never appears in a release commit or tag.

## Cutting a release

1. Set `__version__` to the release version in a release commit on `main` (for example `1.0.0-beta-dev` -> `1.0.0-beta.1`). In the same commit point the README install block at the release's `camlab-rpi-<version>.tar.gz`.
2. Tag that commit and push:

```bash
git tag -a v1.0.0-beta.1 -m "v1.0.0-beta.1"
git push origin v1.0.0-beta.1
```

3. Release workflow builds `camlab-rpi-<version>.tar.gz` (versioned root directory inside) and publishes a GitHub release with generated notes. Tags with a hyphen publish as pre-releases.
4. Bump `__version__` to the next expected version with `-dev` in a follow-up commit.
