import os
import re
from pathlib import Path

from packaging.version import Version

from .exceptions import Error
from .git import Git
from .logger import logger
from .repository import Repository
from .subprocess_wrapper import check_output, subprocess


class Jujutsu(Repository):
    MIN_VERSION = Version("0.28.0")

    @classmethod
    def is_repo(cls, path: str) -> bool:
        """Quick check for repository at specified path."""
        return os.path.exists(os.path.join(path, ".jj"))

    # ----
    # Methods expected from callers of the `Repository` interface:
    # ----

    def __init__(self, path: str):
        self.vcs_version = Jujutsu.__check_and_get_version()

        resolved_path = Path(path).resolve(strict=True)
        logger.debug(f"resolved_path: {resolved_path}")

        try:
            self.git_path = Path(
                check_output(
                    ["jj", "git", "root"], split=False, stderr=subprocess.STDOUT
                )
            )
        except Exception:
            raise ValueError(
                f"{path}: failed to run `jj git root`, likely not a Jujutsu repository"
            )
        logger.debug(f"git_path: {self.git_path}")

        is_colocated = (
            resolved_path == self.git_path.parent and self.git_path.name == ".git"
        )
        if not is_colocated:
            msg = " ".join(
                [
                    f"`jj git root` points to `{self.git_path}`, which we assume to mean",
                    "this is a non-co-located repo. Currently support requires a co-located",
                    "Jujutsu repository. Non-co-located repos will be supported with",
                    "<https://bugzilla.mozilla.org/show_bug.cgi?id=1964150>.",
                ]
            )
            logger.warn(msg)
            raise ValueError(msg)

        self.__git_repo = Git(self.git_path.parent)

        # Populate common fields expected from a `Repository`

        dot_path = os.path.join(path, ".jj")
        if not os.path.exists(dot_path):
            raise ValueError("%s: not a Jujutsu repository" % path)
        logger.debug("found Jujutsu repo in %s", path)
        super().__init__(path, dot_path)

        self.vcs = "jj"

        self.revset = None
        self.branch = None

    @staticmethod
    def __check_and_get_version() -> str:
        min_version = Jujutsu.MIN_VERSION

        version_re = re.compile(r"jj (\d+\.\d+\.\d+)(?:-[a-fA-F0-9]{40})?")
        jj_version_output = check_output(["jj", "version"], split=False)
        m = version_re.fullmatch(jj_version_output)
        if not m:
            raise Error("Failed to determine Jujutsu version.")
        version = Version(m.group(1))

        if version < min_version:
            raise Error(f"`moz-phab` requires Jujutsu {min_version} or higher.")
        return m.group(0)
