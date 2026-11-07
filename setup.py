# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
"""Build hooks: record the git commit hash in the built package.

All project metadata lives in pyproject.toml. This file only adds custom
build_py/sdist commands that write emaprs/_build_info.py into the build
output (never the source tree), so an installed copy can report which
commit it was built from without needing git or a .git directory.

The hash comes from, in order:
  1. $EMAPRS_GIT_HASH, if set (e.g. a BitBake recipe building from a
     tarball can export it from ${SRCREV});
  2. `git rev-parse --short HEAD` in the source tree;
  3. the _build_info.py an sdist was shipped with;
  4. "unknown".
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent
BUILD_INFO = Path("emaprs") / "_build_info.py"


def _git_hash() -> str | None:
    env = os.environ.get("EMAPRS_GIT_HASH", "").strip()
    if env:
        return env[:7] if len(env) == 40 else env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        toplevel, short = out.stdout.split()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    # Ignore an unrelated repo that merely contains the unpacked source
    # (e.g. a Yocto build directory inside a layer checkout).
    return short if Path(toplevel).resolve() == ROOT else None


def _write_build_info(base_dir: Path) -> None:
    git_hash = _git_hash()
    if git_hash is None:
        if (ROOT / BUILD_INFO).exists():
            # Building from an unpacked sdist (no .git): build_py already
            # copied the file the sdist shipped with, so keep it.
            return
        git_hash = "unknown"
    target = base_dir / BUILD_INFO
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Generated at build time by setup.py; do not edit or commit.\n"
        f'GIT_HASH = "{git_hash}"\n',
        encoding="utf-8",
    )


class BuildPyWithInfo(build_py):
    def run(self) -> None:
        super().run()
        # editable_mode only exists on setuptools >= 64.
        if not getattr(self, "editable_mode", False):
            _write_build_info(Path(self.build_lib))


class SdistWithInfo(sdist):
    def make_release_tree(self, base_dir, files) -> None:
        super().make_release_tree(base_dir, files)
        _write_build_info(Path(base_dir))


setup(cmdclass={"build_py": BuildPyWithInfo, "sdist": SdistWithInfo})
