# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import imp
import os
import mock
import pytest

from conftest import hg_out

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


check_call_by_line = mock.Mock(
    side_effect=(
        ["Revision URI: http://example.test/D123"],
        ["Revision URI: http://example.test/D124"],
    )
)


def test_submit_feature_branch(in_process, hg_repo_path):
    testfile = hg_repo_path / "X"
    testfile.write_text(u"a1")
    hg_out("add")
    hg_out("commit", "--message", "A1")
    testfile.write_text(u"b1")
    hg_out("commit", "--message", "B1")
    testfile.write_text(u"b2")
    hg_out("commit", "--message", "B2")
    hg_out("up", "0")
    testfile.write_text(u"a2")
    hg_out("commit", "--message", "A2")
    mozphab.main(["submit", "--yes", "--bug", "1"])
    log = hg_out("log", "--graph", "--template", r"{desc}\n")
    expected = """\
@  Bug 1 - A2
|
|  Differential Revision: http://example.test/D124
| o  B2
| |
| o  B1
|/
o  Bug 1 - A1

   Differential Revision: http://example.test/D123
"""
    assert log == expected
