# coding=utf-8
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from collections import OrderedDict

from .exceptions import Error


def to_llist(revisions):
    """Converts a list to a linked list.

    Parameters:
        revisions (list): a list of revisions to create a linked list

    Returns (dict) a linked list in the form `dict(rev1="rev2", rev2="rev3", rev3=None)`
    where `rev2` is the child of `rev1` and the parent of `rev3` which has no children.
    """
    llist = {}
    for i, revision in enumerate(revisions):
        child = revisions[i + 1] if i + 1 < len(revisions) else None
        llist[revision] = child
    return llist


def walk_llist(llist, allow_multiple_heads=False):
    """Parse the llist for multiple heads and return a unique list of elements.

    Parameters:
        llist (dict): A linked list. {A_id: B_id, B_id: None}
        allow_multiple_heads (bool): Fail if multiple heads found.
    """
    referenced_children = [r for r in llist.values() if r]

    # Find head
    node = None
    for n in sorted(llist.keys()):
        if n not in referenced_children:
            if node:
                if not allow_multiple_heads:
                    raise Error("Multiple heads found.")
                break
            node = n
    if not node:
        raise Error("Failed to find head.")

    # Walk list, checking for loops
    nodes = []
    while node:
        nodes.append(node)
        child = llist.get(node)
        if child and child in nodes:
            raise Error("Dependency loop")

        node = child

    return nodes


def stack_transactions(remote_phids, local_phids):
    """Prepare transactions to set the stack as provided in local_phids.

    Returns (OrderedDict) transactions for PHID as defined in 
    `differential.revision.edit`.
    """
    remote_list = to_llist(remote_phids)
    local_list = to_llist(local_phids)

    transactions = OrderedDict()
    for revision in remote_phids + local_phids:
        transactions[revision] = []

    # Remote child no longer in stack
    for revision in [r for r in remote_phids if r not in local_phids]:
        child = remote_list[revision]
        if child is not None:
            transactions[revision].append(("children.remove", [child]))

        remote_list[revision] = None
        walk_llist(remote_list, allow_multiple_heads=True)

    # New revisions
    for revision in [r for r in local_phids if r not in remote_phids]:
        child = local_list[revision]
        if child is None:
            if revision in remote_list and remote_list[revision] is not None:
                transactions[revision].append(
                    ("children.remove", [remote_list[revision]])
                )
        else:
            transactions[revision].append(("children.set", [child]))

        remote_list[revision] = child
        walk_llist(remote_list, allow_multiple_heads=True)

    # Modify child
    for revision in [r for r in remote_phids if r in local_phids]:
        if local_list[revision] != remote_list[revision]:
            child = local_list[revision]
            if child is None:
                # Remove child if the last child is a parent remotely
                if revision in remote_list and remote_list[revision] is not None:
                    transactions[revision].append(
                        ("children.remove", [remote_list[revision]])
                    )
            else:
                transactions[revision].append(("children.set", [child]))

            remote_list[revision] = child
            walk_llist(remote_list, allow_multiple_heads=True)

    # Abandon
    for revision in [r for r in remote_phids if r not in local_phids]:
        transactions[revision].append(("abandon", True))
        del remote_list[revision]
        walk_llist(remote_list, allow_multiple_heads=True)

    assert ":".join(walk_llist(remote_list)) == ":".join(walk_llist(local_list))

    conduit_transactions = {}
    for revision, transaction_list in transactions.items():
        if not transaction_list:
            continue
        conduit_transactions.setdefault(revision, [])
        for transaction in transaction_list:
            k, v = transaction
            if k == "children.set" and v:
                v = v
            conduit_transactions[revision].append({"type": k, "value": v})

    return conduit_transactions
