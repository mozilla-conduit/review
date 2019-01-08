# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import json
import os
import mock
import pytest

from conftest import hg_out

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


def test_submit_create(in_process, hg_repo_path):
    testfile = hg_repo_path / "X"
    testfile.write_text(u"a")
    hg_out("add")
    hg_out("commit", "--message", "A r?alice")

    mozphab.main(["submit", "--yes", "--bug", "1"])

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """\
Bug 1 - A r?alice

Differential Revision: http://example.test/D123
"""
    assert log == expected


def test_submit_update(in_process, hg_repo_path):
    testfile = hg_repo_path / "X"
    testfile.write_text(u"a")
    hg_out("add")

    # Write out our commit message as if the program had already run and appended
    # a Differential Revision keyword to the commit body for tracking.
    hg_out(
        "commit",
        "--message",
        """\
Bug 1 - A

Differential Revision: http://example.test/D123
""",
    )

    mozphab.main(["submit", "--yes", "--bug", "1"])

    log = hg_out("log", "--template", r"{desc}\n", "--rev", ".")
    expected = """\
Bug 1 - A

Differential Revision: http://example.test/D123
"""
    assert log == expected
