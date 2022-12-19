# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Generate change log to be pasted in the Mozilla wiki and Discourse:
    - https://wiki.mozilla.org/MozPhab/Changelog
    - https://discourse.mozilla.org/c/firefox-tooling-announcements
"""

import argparse
import re
import subprocess
import time

from pathlib import Path
from typing import List

import requests

# If we are `moz-phab/dev/release_announcement.py`, then `moz-phab` is the following.
MOZPHAB_PATH_DEFAULT = Path(__file__).resolve().parent.parent


def fetch_bugzilla_info(bug: str) -> dict:
    """Get bug info from the Bugzilla API
    Args:
        bug (str): A bug ID to query.
    Returns:
        dict: Dictionary containing bug info of the given bug.
    """
    url = f"https://bugzilla.mozilla.org/rest/bug/{bug}"
    response = requests.get(url)

    # TODO -- is there a better way to query individual bugs, or a list of bugs?
    return response.json()["bugs"][0]


def get_bug_ids(
    last_version: str,
    current_version: str,
    mozphab_path: Path,
) -> List[str]:
    """Fetch commits between `last_version` and `current_version` and return Bug IDs."""
    output = subprocess.check_output(
        ["git", "log", "--oneline", f"{last_version}..{current_version}"],
        cwd=mozphab_path,
        encoding="utf-8",
    )

    bug_re = re.compile(r"^.*[Bug|bug] (\d+).*$", flags=re.MULTILINE)

    bug_ids = []
    for line in output.split("\n"):
        bug = bug_re.match(line)
        if bug:
            bug_ids.append(bug.groups()[0])

    bug_ids = list(set(bug_ids))
    bug_ids.sort()
    return bug_ids


def discourse_formatted_text(current_version: str, bug_titles: dict) -> str:
    """Return text formatted for Discourse."""
    out = [f"Bugs resolved in Moz-Phab {current_version}:"]
    for bug, bug_title in bug_titles.items():
        out.append(f"- [bug {bug}](https://bugzilla.mozilla.org/{bug}) {bug_title}")
    out.append("")
    out.append("Discuss these changes in #conduit on Slack or Matrix.")

    return "\n".join(out)


def wiki_formatted_text(current_version: str, bug_titles: dict) -> str:
    """Return text formatted for Mozilla Wiki."""
    out = [f"=== {current_version} ==="]
    for bug, bug_title in bug_titles.items():
        out.append(f"* {{{{bug|{bug}}}}} {bug_title}")

    out.append("")

    return "\n".join(out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "current_version",
        help="Current version of moz-phab to generate announcements for.",
    )
    parser.add_argument("last_version", help="Previously released moz-phab version.")
    parser.add_argument(
        "--mozphab-path",
        help="Path to moz-phab directory.",
        default=MOZPHAB_PATH_DEFAULT,
    )

    args = parser.parse_args()

    bug_ids = get_bug_ids(args.last_version, args.current_version, args.mozphab_path)
    bug_titles = {}

    for bug in bug_ids:
        time.sleep(0.1)
        bug_titles[bug] = fetch_bugzilla_info(bug)["summary"]

    print()
    print(f"{'*' * 32} >8 copy and paste to wiki {'*' * 32}")
    print(wiki_formatted_text(args.current_version, bug_titles))

    print(f"{'*' * 32} >8 copy and paste to discourse {'*' * 32}")
    print(discourse_formatted_text(args.current_version, bug_titles))
