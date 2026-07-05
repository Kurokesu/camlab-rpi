# Releasing

Maintainer notes for versioning and cutting releases.

## Version line

- Pre-stable: plain SemVer `0.x.y`, no suffix. The `0.` major already signals that anything may change.
- Between releases `__version__` in `camlab/__init__.py` carries the next version with `-dev` (for example `0.2.0-dev`). The GUI status strip shows it, so dev builds identify themselves.
- Stabilization: `v1.0.0-beta.N` pre-releases when feature-complete, `v1.0.0` when field-proven.
- Post-1.0: MAJOR breaks existing installs (reflash or manual migration), MINOR adds features, PATCH fixes bugs.

## Cutting a release

1. Drop `-dev` from `__version__` in a release commit on `main` (for example `0.2.0-dev` -> `0.2.0`).
2. Tag that commit with an annotated tag and push it:

```bash
git tag -a v0.2.0 -m "v0.2.0"
git push origin v0.2.0
```

3. The release workflow builds zip file and publishes GitHub release with generated notes. It fails if the tag does not match `__version__`, fix the mismatch and re-tag.
4. Bump `__version__` to the next expected minor with `-dev` (for example `0.3.0-dev`) in a follow-up commit. An intervening patch release may change the plan.

Tags with a hyphen (`v1.0.0-beta.1`) are published as pre-releases automatically.
