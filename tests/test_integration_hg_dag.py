# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from .conftest import hg_out

from unittest import mock

from mozphab import mozphab


_revision = 100

call_conduit = mock.Mock()


def _init_repo(hg_repo_path):
    test_file = hg_repo_path / "X"
    test_file.write_text("R0")
    hg_out("commit", "--addremove", "--message", "R0")
    return dict(test_file=test_file, rev=1, rev_map={"R0": "1"})


def _add_commit(repo, parent, name):
    hg_out("update", repo["rev_map"][parent])
    repo["test_file"].write_text(name)
    hg_out("commit", "--message", name)
    repo["rev"] += 1
    repo["rev_map"][name] = str(repo["rev"])


def _checkout(repo, name):
    hg_out("update", repo["rev_map"][name])


def _submit(repo, start, end, expected, wip=False):
    mozphab.main(
        [
            "submit",
            "--yes",
            *(["--wip"] if wip else []),
            "--bug",
            "1",
            repo["rev_map"][start],
            repo["rev_map"][end],
        ],
        is_development=True,
    )
    log = hg_out("log", "--graph", "--template", r"{desc|firstline}\n")
    assert log.strip() == expected.strip()


def _conduit_side_effect(calls=1):
    side_effect = [
        # ping
        dict(),
        # diffusion.repository.search
        dict(data=[dict(phid="PHID-REPO-1", fields=dict(vcs="hg"))]),
    ]

    for i in range(calls):
        side_effect.extend(
            [
                # differential.creatediff
                dict(dict(phid="PHID-DIFF-{}".format(i), diffid=str(i))),
                # differential.setdiffproperty
                dict(),
                # differential.revision.edit
                dict(object=dict(id=str(123 + i))),
                # differential.setdiffproperty
                dict(),
            ]
        )

    return side_effect


def test_submit_wip(in_process, hg_repo_path):
    repo = _init_repo(hg_repo_path)

    call_conduit.side_effect = _conduit_side_effect()  # TODO verify bool

    _add_commit(repo, "R0", "A1")
    _submit(
        repo,
        "A1",
        "A1",
        """
@  Bug 1 - A1
|
o  R0
|
o  init
""",
        wip=True,
    )
    # Get the API call arguments for the `differential.revision.edit` call.
    edit_call_api_call_args = next(
        args[1]
        for _name, args, _kwargs in call_conduit.mock_calls
        if args[0] == "differential.revision.edit"
    )
    # Get the value of the `plan-changes` transaction.
    wip_transaction = next(
        transaction
        for transaction in edit_call_api_call_args["transactions"]
        if transaction["type"] == "plan-changes"
    )
    assert (
        wip_transaction["value"] is True
    ), "`wip=True` in `submit` should set `plan-changes`."


def test_submit_single_1(in_process, hg_repo_path):
    repo = _init_repo(hg_repo_path)

    call_conduit.side_effect = _conduit_side_effect()

    _add_commit(repo, "R0", "A1")
    _add_commit(repo, "A1", "B1")
    _add_commit(repo, "B1", "C1")
    _submit(
        repo,
        "A1",
        "A1",
        """
@  C1
|
o  B1
|
o  Bug 1 - A1
|
o  R0
|
o  init
""",
    )


def test_submit_single_2(in_process, hg_repo_path):
    repo = _init_repo(hg_repo_path)

    call_conduit.side_effect = _conduit_side_effect()

    _add_commit(repo, "R0", "A1")
    _add_commit(repo, "A1", "B1")
    _add_commit(repo, "A1", "B2")
    _submit(
        repo,
        "A1",
        "A1",
        """
@  B2
|
| o  B1
|/
o  Bug 1 - A1
|
o  R0
|
o  init
""",
    )


def test_submit_single_3(in_process, hg_repo_path):
    repo = _init_repo(hg_repo_path)

    call_conduit.side_effect = _conduit_side_effect()

    _add_commit(repo, "R0", "A1")
    _add_commit(repo, "A1", "B1")
    _add_commit(repo, "B1", "C1")
    _add_commit(repo, "B1", "C2")
    _submit(
        repo,
        "A1",
        "A1",
        """
@  C2
|
| o  C1
|/
o  B1
|
o  Bug 1 - A1
|
o  R0
|
o  init
""",
    )


def test_submit_stack_1(in_process, hg_repo_path):
    repo = _init_repo(hg_repo_path)

    call_conduit.side_effect = _conduit_side_effect(2)

    _add_commit(repo, "R0", "A1")
    _add_commit(repo, "A1", "B1")
    _checkout(repo, "A1")
    _submit(
        repo,
        "A1",
        "B1",
        """
o  Bug 1 - B1
|
@  Bug 1 - A1
|
o  R0
|
o  init
""",
    )


def test_submit_stack_2(in_process, hg_repo_path):
    repo = _init_repo(hg_repo_path)

    call_conduit.side_effect = _conduit_side_effect(2)

    _add_commit(repo, "R0", "A1")
    _add_commit(repo, "A1", "B1")
    _add_commit(repo, "A1", "B2")
    _submit(
        repo,
        "A1",
        "B1",
        """
o  Bug 1 - B1
|
| @  B2
|/
o  Bug 1 - A1
|
o  R0
|
o  init
""",
    )


def test_submit_stack_3(in_process, hg_repo_path):
    repo = _init_repo(hg_repo_path)

    call_conduit.side_effect = _conduit_side_effect(2)

    _add_commit(repo, "R0", "A1")
    _add_commit(repo, "A1", "B1")
    _add_commit(repo, "A1", "B2")
    _add_commit(repo, "B1", "C1")
    _submit(
        repo,
        "A1",
        "B1",
        """
@  C1
|
o  Bug 1 - B1
|
| o  B2
|/
o  Bug 1 - A1
|
o  R0
|
o  init
""",
    )
