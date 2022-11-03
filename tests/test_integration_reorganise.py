# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
from unittest import mock
import pytest

from .conftest import git_out, hg_out

from mozphab import exceptions, mozphab


call_conduit = mock.Mock()


def test_no_need_to_reorganise(in_process, git_repo_path, init_sha):
    # One commit
    call_conduit.side_effect = (
        dict(),  # ping
        dict(data=[dict(phid="PHID-1", id=1)]),  # differential.get_revision
        # Revision is not related to any other revision. There is no stack.
        dict(data=[]),  # edge.search
    )

    f = git_repo_path / "X"
    f.write_text("A")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: A r?alice

Differential Revision: http://example.test/D1
"""
    )
    git_out("commit", "--file", "msg")
    with pytest.raises(exceptions.Error) as e:
        mozphab.main(["reorg", "--yes", init_sha], is_development=True)

    assert (str(e.value)) == "Reorganisation is not needed."

    # Stack of commits
    call_conduit.side_effect = (
        dict(data=[dict(phid="PHID-1", id=1), dict(phid="PHID-2", id=2)]),
        # PHID-2 is the only child of PHID-1.
        dict(
            data=[
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-2",
                    edgeType="revision.child",
                )
            ]
        ),
    )

    f.write_text("B")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: B r?alice

Differential Revision: http://example.test/D2
"""
    )
    git_out("commit", "-a", "--file", "msg")

    with pytest.raises(exceptions.Error) as e:
        mozphab.main(["reorg", "--yes", init_sha], is_development=True)

    assert (str(e.value)) == "Reorganisation is not needed."


def test_new_separate_revisions_to_stack(in_process, git_repo_path, init_sha):
    call_conduit.side_effect = (
        # ping
        dict(),
        # search revisions
        dict(
            data=[
                dict(
                    phid="PHID-1",
                    id=1,
                    fields=dict(status=dict(value="needs-review")),
                ),
                dict(
                    phid="PHID-2",
                    id=2,
                    fields=dict(status=dict(value="needs-review")),
                ),
            ]
        ),
        # edge search
        dict(data=[]),
        # differential.edit_revision
        dict(data=[dict(phid="PHID-1", id=1)]),
    )

    f = git_repo_path / "X"
    f.write_text("A")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: A r?alice

Differential Revision: http://example.test/D1
"""
    )
    git_out("commit", "--file", "msg")
    f.write_text("B")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: B r?alice

Differential Revision: http://example.test/D2
"""
    )
    git_out("commit", "-a", "--file", "msg")
    mozphab.main(["reorg", "--yes", init_sha], is_development=True)
    assert (
        mock.call(
            "differential.revision.edit",
            {
                "objectIdentifier": "PHID-1",
                "transactions": [{"type": "children.set", "value": ["PHID-2"]}],
            },
        )
        in call_conduit.call_args_list
    )


def test_add_revision_existing_stack(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        # search revisions
        dict(
            data=[
                dict(
                    phid="PHID-1",
                    id=1,
                    fields=dict(status=dict(value="needs-review")),
                ),
                dict(
                    phid="PHID-2",
                    id=2,
                    fields=dict(status=dict(value="needs-review")),
                ),
                dict(
                    phid="PHID-3",
                    id=3,
                    fields=dict(status=dict(value="needs-review")),
                ),
            ]
        ),
        # edge search
        dict(
            data=[
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-2",
                    edgeType="revision.child",
                )
            ]
        ),  # differential.edge.search
        dict(
            data=[
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-2",
                    edgeType="revision.parent",
                )
            ]
        ),  # differential.edge.search
        # differential.edit_revision
        dict(data=[dict(phid="PHID-1", id=1)]),
        dict(data=[dict(phid="PHID-3", id=1)]),
        dict(data=[dict(phid="PHID-3", id=1)]),
    )

    f = git_repo_path / "X"
    f.write_text("A")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: A r?alice

Differential Revision: http://example.test/D1
"""
    )
    git_out("commit", "--file", "msg")
    fn = git_repo_path / "Y"
    fn.write_text("C")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: C r?alice

Differential Revision: http://example.test/D3
"""
    )
    git_out("commit", "--file", "msg")
    f.write_text("B")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: B r?alice

Differential Revision: http://example.test/D2
"""
    )
    git_out("commit", "-a", "--file", "msg")
    mozphab.main(["reorg", "--yes", init_sha], is_development=True)
    assert (
        mock.call(
            "differential.revision.edit",
            {
                "objectIdentifier": "PHID-1",
                "transactions": [{"type": "children.set", "value": ["PHID-3"]}],
            },
        )
        in call_conduit.call_args_list
    )
    assert (
        mock.call(
            "differential.revision.edit",
            {
                "objectIdentifier": "PHID-3",
                "transactions": [{"type": "children.set", "value": ["PHID-2"]}],
            },
        )
        in call_conduit.call_args_list
    )


def test_add_revision_existing_stack_hg(in_process, hg_repo_path):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        # ping
        dict(),
        # search revisions
        dict(
            data=[
                dict(
                    phid="PHID-1",
                    id=1,
                    fields=dict(status=dict(value="needs-review")),
                ),
                dict(
                    phid="PHID-2",
                    id=2,
                    fields=dict(status=dict(value="needs-review")),
                ),
                dict(
                    phid="PHID-3",
                    id=3,
                    fields=dict(status=dict(value="needs-review")),
                ),
            ]
        ),
        # edge search
        dict(
            data=[
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-2",
                    edgeType="revision.child",
                ),
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-3",
                    edgeType="revision.child",
                ),
            ]
        ),  # differential.edge.search
        dict(
            data=[
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-2",
                    edgeType="revision.parent",
                )
            ]
        ),  # differential.edge.search
        dict(
            data=[
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-3",
                    edgeType="revision.parent",
                )
            ]
        ),  # differential.edge.search
    )

    f = hg_repo_path / "X"
    f.write_text("A")
    hg_out("add")
    msgfile = hg_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: A r?alice

Differential Revision: http://example.test/D1
"""
    )
    hg_out("commit", "-l", "msg")
    fn = hg_repo_path / "Y"
    fn.write_text("C")
    hg_out("add")
    msgfile = hg_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: C r?alice

Differential Revision: http://example.test/D3
"""
    )
    hg_out("commit", "-l", "msg")
    f.write_text("B")
    msgfile = hg_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: B r?alice

Differential Revision: http://example.test/D2
"""
    )
    hg_out("commit", "-l", "msg")
    with pytest.raises(exceptions.Error) as e:
        mozphab.main(["reorg", "--yes", "1", "3"], is_development=True)

    assert (str(e.value)) == "Revision D1 has multiple children."


def test_abandon_a_revision(in_process, git_repo_path, init_sha):
    call_conduit.reset_mock()
    call_conduit.side_effect = (
        dict(),  # ping
        dict(
            data=[
                dict(
                    phid="PHID-1",
                    id=1,
                    fields=dict(status=dict(value="needs-review")),
                )
            ]
        ),  # differential.revision.search
        dict(
            data=[
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-2",
                    edgeType="revision.child",
                )
            ]
        ),  # differential.edge.search
        dict(
            data=[
                dict(
                    sourcePHID="PHID-1",
                    destinationPHID="PHID-2",
                    edgeType="revision.parent",
                )
            ]
        ),  # differential.edge.search
        dict(
            data=[
                dict(
                    phid="PHID-2",
                    id=2,
                    fields=dict(status=dict(value="needs-review")),
                )
            ]
        ),  # differential.revision.search
        dict(data=[dict(phid="PHID-1", id=1)]),  # differential.edit_revision
        dict(data=[dict(phid="PHID-2", id=2)]),  # differential.edit_revision
    )

    f = git_repo_path / "X"
    f.write_text("A")
    git_out("add", ".")
    msgfile = git_repo_path / "msg"
    msgfile.write_text(
        """\
Bug 1: A r?alice

Differential Revision: http://example.test/D1
"""
    )
    git_out("commit", "--file", "msg")
    mozphab.main(["reorg", "--yes", init_sha], is_development=True)
    # Search for the revision to get its PHID
    assert call_conduit.call_args_list[1] == mock.call(
        "differential.revision.search",
        {"constraints": {"ids": [1]}, "attachments": {"reviewers": True}},
    )
    # Search for direct related revisions of PHID-2
    assert call_conduit.call_args_list[2] == mock.call(
        "edge.search",
        {
            "sourcePHIDs": ["PHID-1"],
            "types": ["revision.parent", "revision.child"],
            "limit": 10000,
        },
    )
    # Search for direct related revisions of PHID-1
    assert call_conduit.call_args_list[3] == mock.call(
        "edge.search",
        {
            "sourcePHIDs": ["PHID-2"],
            "types": ["revision.parent", "revision.child"],
            "limit": 10000,
        },
    )
    # Search for revisions in the stack
    assert call_conduit.call_args_list[4] == mock.call(
        "differential.revision.search",
        {"constraints": {"phids": ["PHID-2"]}, "attachments": {"reviewers": True}},
    )
    # Remove the child from PHID-1 and abandon PHID-1
    assert call_conduit.call_args_list[5] == mock.call(
        "differential.revision.edit",
        {
            "transactions": [{"type": "children.remove", "value": ["PHID-2"]}],
            "objectIdentifier": "PHID-1",
        },
    )
    assert call_conduit.call_args_list[6] == mock.call(
        "differential.revision.edit",
        {
            "transactions": [{"type": "abandon", "value": True}],
            "objectIdentifier": "PHID-2",
        },
    )
