# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import os

from .exceptions import Error
from .git import Git
from .jujutsu import Jujutsu
from .mercurial import Mercurial
from .repository import DEFAULT_REPOSITORY_OPTIONS, Repository, RepositoryOptions


def find_repo_root(path: str, avoid_jj: bool = False) -> str | None:
    """Lightweight check for a repo in/under the specified path."""
    path = os.path.abspath(path)
    while os.path.split(path)[1]:
        if (
            Mercurial.is_repo(path)
            or (Jujutsu.is_repo(path) and not avoid_jj)
            or Git.is_repo(path)
        ):
            return path
        path = os.path.abspath(os.path.join(path, os.path.pardir))
    return None


def probe_repo(
    path: str,
    avoid_jj: bool = False,
    options: RepositoryOptions = DEFAULT_REPOSITORY_OPTIONS,
) -> Repository | None:
    """Attempt to find a repository at `path`."""
    try:
        return Mercurial(path, options=options)
    except ValueError:
        pass

    # NOTE: Jujutsu may have a co-located `.git` directory, so let's detect a `.jj` directory first.
    if not avoid_jj:
        try:
            return Jujutsu(path, options=options)
        except ValueError:
            pass

    try:
        return Git(path, options=options)
    except ValueError:
        pass

    return None


def open_repository(options: RepositoryOptions) -> Repository:
    """Return the configured repository at `options.path`, or above the cwd."""

    repo = None

    avoid_jj_vcs = options.avoid_jj

    def vcses_searched():
        jj = [] if avoid_jj_vcs else [".jj"]
        vcses_searched = [".hg"] + jj + [".git"]
        vcses_searched = " / ".join(vcses_searched)
        return vcses_searched

    # This allows users to override the below sanity checks.
    if options.path:
        repo = probe_repo(options.path, avoid_jj=avoid_jj_vcs, options=options)
        if not repo:
            raise Error("%s: Not a repository: %s" % (options.path, vcses_searched()))

    else:
        # Walk parents to find repository root.
        path = find_repo_root(os.getcwd(), avoid_jj_vcs)
        if path:
            repo = probe_repo(path, avoid_jj_vcs, options=options)
        if not repo:
            raise Error(
                "Not a repository (or any of the parent directories): %s"
                % vcses_searched()
            )

    repo.configure()
    return repo
