# Releasing

Maintainer notes for versioning and cutting releases.

## Version line

- SemVer. MAJOR breaks existing installs (reflash or manual migration), MINOR adds features, PATCH fixes bugs.
- Stabilization goes through `v1.0.0-beta.N` pre-releases when feature-complete, `v1.0.0` when field-proven.
- Between releases `__version__` in `camlab/__init__.py` carries the next version with `-dev` (for example `1.0.0-beta-dev`). The GUI status strip shows it, so test builds identify themselves. `-dev` never appears in a release commit or tag.

## Cutting a release

1. Set `__version__` to release version in a release commit on `main` (for example `1.0.0-beta-dev` -> `1.0.0-beta.1`).
2. Tag that commit and push:

```bash
git tag -a v1.0.0-beta.1 -m "v1.0.0-beta.1"
git push origin v1.0.0-beta.1
```

3. Release workflow builds `camlab-rpi-<version>.tar.gz` (versioned root directory inside) and publishes a GitHub release with generated notes. Tags with a hyphen publish as pre-releases.
4. Bump `__version__` to the next expected version with `-dev` in a follow-up commit.

## Debian package

The deb ships from `debian/latest`, a packaging-only branch (DEP-14, recipe and workflows, never merged with `main`). After the source release exists:

1. On `debian/latest`, open a `debian/changelog` entry for the release version in Debian form (`-` pre-release separator becomes `~`, for example `1.0.0~beta.3-1`). Sync `debian/control` Depends with `scripts/setup/deps.sh` and Suggests with drivers `drivers.sh` installs. Commit, push, wait for green CI.
2. Tag that commit and push (`~` becomes `_` in tags):

```bash
git tag -a debian/1.0.0_beta.3-1 -m "debian/1.0.0_beta.3-1"
git push origin debian/1.0.0_beta.3-1
```

3. Release workflow verifies the paired `v` tag, builds against it and uploads `camlab-rpi_<version>.tar.gz` plus signed `SHA256SUMS` onto that release, next to the source tarball. A packaging-only rebuild increments Debian revision after the hyphen, each one a new changelog entry and its own tag.
4. Publish into [apt.kurokesu.com](https://apt.kurokesu.com) with a manifest entry in `Kurokesu/apt`, which ingests those assets.

See `debian/source/README.source` on `debian/latest` for layout and details.
