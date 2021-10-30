# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

#import pytest

from mozphab.commands.uplift import (
    list_trains,
    map_train_arg_to_repo,
)
from mozphab.helpers import (
    move_drev_to_original,
)


def test_map_train_arg_to_repo():
    train = "SECONDREPO"
    map_train_arg_to_repo(train)
    assert False


def test_list_trains():
    #list_trains()
    assert True


def test_move_drev_to_original():
    # Patches with only `Differential Revision` should move to
    # `Original Revision` and return `None` for `rev-id`
    assert move_drev_to_original(
        "Bug 1: test r?ConduitReviewer\n"
        "\n"
        "Differential Revision: http://phabricator.test/D1\n", 1 
    ) == (
        "Bug 1: test r?ConduitReviewer\n"
        "\n"
        "Original Revision: http://phabricator.test/D1\n", None 
    )

    # Patches with both `Original` and `Differential` revision stay the same
    assert move_drev_to_original(
        "Bug 1: test r?ConduitReviewer\n"
        "\n"
        "Original Revision: http://phabricator.test/D1\n"
        "\n"
        "Differential Revision: http://phabricator.test/D2\n", 2
    ) == (
        "Bug 1: test r?ConduitReviewer\n"
        "\n"
        "Original Revision: http://phabricator.test/D1\n"
        "\n"
        "Differential Revision: http://phabricator.test/D2\n", 2
    )

