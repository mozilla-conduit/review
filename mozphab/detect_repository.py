# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import os

from typing import Optional

from .exceptions import Error
from .git import Git
from .mercurial import Mercurial
from .repository import Repository


def find_repo_root(path: str) -> Optional[str]:
    """Lightweight check for a repo in/under the specified path."""
    path = os.path.abspath(path)
    while os.path.split(path)[1]:
        if Mercurial.is_repo(path) or Git.is_repo(path):
            return path
        path = os.path.abspath(os.path.join(path, os.path.pardir))
    return None


def probe_repo(path: str) -> Optional[Repository]:
    """Attempt to find a repository at `path`."""
    try:
        return Mercurial(path)
    except ValueError:
        pass

    try:
        return Git(path)
    except ValueError:
        pass

    return None


def repo_from_args(args: argparse.Namespace) -> Repository:
    """Returns a Repository object from either args.path or the cwd"""

    repo = None

    # This allows users to override the below sanity checks.
    if hasattr(args, "path") and args.path:
        repo = probe_repo(args.path)
        if not repo:
            raise Error("%s: Not a repository: .hg / .git" % args.path)

    else:
        # Walk parents to find repository root.
        path = find_repo_root(os.getcwd())
        if path:
            repo = probe_repo(path)
        if not repo:
            raise Error(
                "Not a repository (or any of the parent directories): .hg / .git"
            )

    repo.set_args(args)
    return repo
