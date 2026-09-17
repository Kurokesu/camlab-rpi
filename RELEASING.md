# Releasing

Maintainer notes for versioning and cutting releases.

## Version line

- SemVer. MAJOR breaks existing installs (reflash or manual migration), MINOR adds features, PATCH fixes bugs.
- Stabilization goes through `v1.0.0-beta.N` pre-releases when feature-complete, `v1.0.0` when field-proven.
- Between releases `__version__` in `camlab/__init__.py` carries next version with `-dev` (for example `1.0.0-beta-dev`). GUI status strip shows it, so test builds identify themselves. `-dev` never appears in a release commit or tag.

## Camera stack pin

`libcamera` and `rpicam-apps` fork versions a release is validated against are pinned in `apt-packages`, floor and ceiling per package. Range covers packaging rebuilds, so `1:1.13.0+krks1-2` lands on fresh installs while `1:1.13.0+krks2-1` waits for pin to move.

To move it:

1. Edit `apt-packages` in release commit.
2. Run `scripts/dev/check-stack-pin.sh` on a Pi carrying Kurokesu archive.
3. Install candidate stack on a test unit and run camlab against it. Check that manual exposure, gain and white balance land in metadata, modes reconfigure and viewfinder renders. Re-run the check afterwards.
4. Mention it in `debian/changelog` for that release.

## Cutting a release

1. Confirm `apt-packages` names stack this release was validated against.
2. Set `__version__` to release version in a release commit on `main` (for example `1.0.0-beta-dev` to `1.0.0-beta.1`).
3. Tag that commit and push:

```bash
git tag -a v1.0.0-beta.1 -m "v1.0.0-beta.1"
git push origin v1.0.0-beta.1
```

4. Release workflow drafts a GitHub release with source tarball attached. Tags with a hyphen carry pre-release flag.
5. Bump `__version__` to next expected version with `-dev` in a follow-up commit.

Release stays a draft until deb lands on it. Publishing is the one notification watchers get, so it goes out with final description and every asset attached.

## Debian package

Deb ships from `debian/latest`, procedure in `debian/source/README.source` there. What that doc does not detail:

1. Sync `debian/control` Suggests with drivers `drivers.sh` installs, in changelog commit.
2. Tag `origin/debian/latest` (`~` becomes `_` in tags):

```bash
git fetch origin
git tag -a debian/1.0.0_beta.3-1 -m "debian/1.0.0_beta.3-1" origin/debian/latest
git push origin debian/1.0.0_beta.3-1
```

3. Once assets land, rewrite draft description and publish, then add a manifest entry in `Kurokesu/apt` to serve it from [apt.kurokesu.com](https://apt.kurokesu.com). Ingest reads published releases, so publishing comes first.
