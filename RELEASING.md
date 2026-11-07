<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- Copyright (C) 2026 Nicholas Heath -->

# Releasing emaprs

Maintainer notes for cutting a release.

1. Bump `__version__` in `emaprs/__init__.py`.
2. Move the `[Unreleased]` entries in `CHANGELOG.md` under a new
   `## [x.y.z] - YYYY-MM-DD` heading, dated the day you tag. That entry
   becomes the release text.
3. Regenerate the screenshots (`tools/screenshots.py`) so the page header
   and Display Options show the new version, and commit them with the
   bump.
4. Push to `master` and wait for CI to pass on every Python version.
5. Tag that commit and push the tag:
   `git tag -a vx.y.z -m "emaprs x.y.z" && git push origin vx.y.z`.
6. The Release workflow (`.github/workflows/release.yml`) checks that the
   tag matches `__version__` and that `CHANGELOG.md` has an entry for it,
   runs `tools/check.sh`, builds the sdist and wheel, and creates a
   **draft** GitHub release with them, `SHA256SUMS`, and the changelog
   entry as its text.
7. Review the draft on GitHub and publish it.

## To do: publishing to PyPI

Not set up yet: releases go to GitHub only for now. To add PyPI, so that
publishing a GitHub release also uploads it to PyPI:

1. On pypi.org, under **Your account → Publishing**, add a pending
   trusted publisher: project `emaprs`, owner `nheathlabs`, repository
   `emaprs`, workflow `pypi.yml`, environment `pypi`. The first upload
   then creates the project; no API token is needed. Until then, anyone
   can register the name `emaprs` on PyPI.
2. In the GitHub repository, **Settings → Environments**, create an
   environment named `pypi`. Adding yourself as a required reviewer
   there makes every PyPI upload wait for one more approval.
3. Add the workflow below as `.github/workflows/pypi.yml`.
4. Add a step after step 7 above: publishing the release runs the PyPI
   workflow, which uploads the release's own sdist and wheel after
   checking them against `SHA256SUMS`. A version on PyPI can't be
   replaced or deleted and re-uploaded, so reviewing the draft is the
   last chance to catch a mistake.
5. Mention PyPI in the comment at the top of `release.yml`, then delete
   this section.

```yaml
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
#
# Publishing a GitHub release uploads its sdist and wheel to PyPI: the
# exact files attached to the release (built by release.yml), checked
# against its SHA256SUMS first. Uses PyPI trusted publishing, so there is
# no token to store; it needs a one-time setup on pypi.org and a "pypi"
# environment in the repository settings. See RELEASING.md.
#
# A PyPI upload can't be replaced or undone, which is why this waits for
# the release to be published by hand rather than running on the tag.

name: PyPI

on:
  release:
    types: [published]

permissions:
  contents: read

jobs:
  upload:
    name: Upload to PyPI
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/project/emaprs/
    permissions:
      contents: read
      id-token: write  # trusted publishing
    env:
      GH_TOKEN: ${{ github.token }}
    steps:
      - name: Download the release's files
        run: |
          gh release download "$GITHUB_REF_NAME" --repo "$GITHUB_REPOSITORY" \
            --dir dist --pattern 'emaprs-*' --pattern SHA256SUMS
      - name: Check them against SHA256SUMS
        run: |
          cd dist
          sha256sum -c SHA256SUMS
          rm SHA256SUMS
          ls -l
      - uses: pypa/gh-action-pypi-publish@release/v1
```

## Building the release files without a release

Run the Release workflow by hand (**Actions → Release → Run workflow**):
it runs the checks and builds the same files as an artifact, without a
release. Locally, `.venv/bin/python -m build` writes them to `dist/`, and
`tools/check_wheel.py dist/*.whl` checks the wheel.
