# Releasing

Maintainer notes for versioning and cutting releases.

## Version line

- SemVer. MAJOR breaks existing installs (reflash or manual migration), MINOR adds features, PATCH fixes bugs.
- Stabilization goes through `v1.0.0-beta.N` pre-releases when feature-complete, `v1.0.0` when field-proven.
- Between releases `__version__` in `camlab/__init__.py` carries the next version with `-dev` (for example `1.0.0-beta-dev`). The GUI status strip shows it, so test builds identify themselves. `-dev` never appears in a release commit or tag.

## Camera stack pin

`camera-stack.version` names `libcamera` and `rpicam-apps` fork versions a release is validated against. `scripts/setup/deps.sh` sources it on a clone install and `debian/rules` on `debian/latest` renders it into deb Depends, floor and ceiling per package.

Range covers packaging rebuilds of pinned release, so `1:1.13.0+krks1-2` lands on fresh installs while `1:1.13.0+krks2-1` waits for pin to move.

To move it:

1. Install candidate stack on a bench box and run camlab against it. Check that manual exposure, gain and white balance land in metadata, modes reconfigure and viewfinder renders.
2. Edit `camera-stack.version` in release commit.
3. Mention it in `debian/changelog` for that release.

## Cutting a release

1. Confirm `camera-stack.version` names stack this release was validated against.
2. Set `__version__` to release version in a release commit on `main` (for example `1.0.0-beta-dev` -> `1.0.0-beta.1`).
3. Tag that commit and push:

```bash
git tag -a v1.0.0-beta.1 -m "v1.0.0-beta.1"
git push origin v1.0.0-beta.1
```

4. Release workflow builds `camlab-rpi-<version>.tar.gz` (versioned root directory inside) and publishes a GitHub release with generated notes. Tags with a hyphen publish as pre-releases.
5. Bump `__version__` to the next expected version with `-dev` in a follow-up commit.

## Debian package

The deb ships from `debian/latest`, a packaging-only branch (DEP-14, recipe and workflows, never merged with `main`). After the source release exists:

1. On `debian/latest`, open a `debian/changelog` entry for the release version in Debian form (`-` pre-release separator becomes `~`, for example `1.0.0~beta.3-1`). Sync `debian/control` Depends with `scripts/setup/deps.sh` and Suggests with drivers `drivers.sh` installs, camera stack relations excepted. Commit, push, wait for green CI.
2. Tag the remote ref and push (`~` becomes `_` in tags):

```bash
git fetch origin
git tag -a debian/1.0.0_beta.3-1 -m "debian/1.0.0_beta.3-1" origin/debian/latest
git push origin debian/1.0.0_beta.3-1
```

3. Release workflow verifies the paired `v` tag, builds against it and uploads `camlab-rpi_<version>.tar.gz` plus signed `SHA256SUMS` onto that release, next to the source tarball. A packaging-only rebuild increments Debian revision after the hyphen, each one a new changelog entry and its own tag.
4. Publish into [apt.kurokesu.com](https://apt.kurokesu.com) with a manifest entry in `Kurokesu/apt`, which ingests those assets.

See `debian/source/README.source` on `debian/latest` for layout and details.
