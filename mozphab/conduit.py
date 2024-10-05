# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import base64
import concurrent.futures
import datetime
import hashlib
import json
import operator
import os
import urllib.parse as url_parse
import urllib.request as url_request
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from .commits import Commit
from .diff import Diff
from .environment import INSTALL_CERT_MSG, USER_AGENT
from .exceptions import (
    CommandError,
    Error,
    NonLinearException,
    NotFoundError,
)
from .helpers import (
    get_arcrc_path,
    read_json_field,
    revision_title_from_commit,
    strip_differential_revision,
)
from .logger import logger
from .simplecache import cache


def normalise_reviewer(reviewer: str, strip_group: bool = True) -> str:
    """This provide a canonical form of the reviewer for comparison."""
    reviewer = reviewer.rstrip("!").lower()
    if strip_group:
        reviewer = reviewer.lstrip("#")
    return reviewer


class ConduitAPIError(Error):
    """Raised when the Phabricator Conduit API returns an error response."""

    def __init__(self, msg: Optional[str] = None):
        super().__init__(f"Phabricator Error: {msg if msg else 'Unknown Error'}")


class ConduitAPI:
    def __init__(self):
        self.repo = None

    def set_repo(self, repo):
        self.repo = repo

    @property
    def repo_phid(self) -> str:
        return self.repo.phid

    def load_api_token(self) -> str:
        """Return an API Token for the given repository.

        Returns:
            API Token string
        """

        if "api_token" in cache:
            return str(cache.get("api_token"))

        token = read_json_field(
            [get_arcrc_path()], ["hosts", self.repo.api_url, "token"]
        )
        if not token:
            raise ConduitAPIError(INSTALL_CERT_MSG)
        cache.set("api_token", token)
        return token

    def save_api_token(self, token: str):
        filename = get_arcrc_path()
        created = False
        try:
            with open(filename, "r", encoding="utf-8") as f:
                rc = json.load(f)
        except FileNotFoundError:
            rc = {}
            created = True

        rc.setdefault("hosts", {})
        rc["hosts"].setdefault(self.repo.api_url, {})
        rc["hosts"][self.repo.api_url]["token"] = token

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(rc, f, sort_keys=True, indent=2)

        if created:
            os.chmod(filename, 0o600)

    def call(
        self, api_method: str, api_call_args: dict, *, api_token: Optional[str] = None
    ) -> dict:
        """Call Conduit API and return the JSON API call result.

        Args:
            api_method: The API method name to call, like 'differential.revision.edit'.
            api_call_args: JSON dict of call args to send.
            api_token: Use specific token to authentication instead of saved value.

        Returns:
            JSON API call result object

        Raises:
            ConduitAPIError if the API threw an error back at us.
        """
        req_args = self._build_request(
            method=api_method,
            args=api_call_args,
            token=api_token,
        )
        logger.debug("%s %s", req_args["url"], api_call_args)

        with url_request.urlopen(url_request.Request(**req_args)) as r:
            res = json.load(r)
        if res["error_code"]:
            raise ConduitAPIError(res.get("error_info", "Error %s" % res["error_code"]))
        return res["result"]

    def _build_request(
        self, *, method: str, args: dict, token: Optional[str]
    ) -> Dict[str, Any]:
        """Return dict with Request args for calling the specified conduit method."""
        return {
            "url": url_parse.urljoin(self.repo.api_url, method),
            "method": "POST",
            "headers": {"User-Agent": USER_AGENT},
            "data": url_parse.urlencode(
                {
                    "params": json.dumps(
                        {
                            **args,
                            "__conduit__": {"token": token or self.load_api_token()},
                        },
                        separators=(",", ":"),
                    ),
                    "output": "json",
                    "__conduit__": True,
                }
            ).encode(),
        }

    def ping(self) -> bool:
        """Sends a ping to the Phabricator server using `conduit.ping` API.

        Returns: `True` if no error, otherwise - `False`
        """
        try:
            self.call("conduit.ping", {})
        except ConduitAPIError as err:
            logger.error(err)
            return False
        except CommandError as err:
            logger.error(err)
            return False
        return True

    def check(self) -> bool:
        """Check if raw Conduit API can be used."""
        # Check if the cache file exists
        path = os.path.join(self.repo.dot_path, ".moz-phab_conduit-configured")
        if os.path.isfile(path):
            return True

        if self.ping():
            # Create the cache file
            with open(path, "a"):
                os.utime(path, None)
            return True

        return False

    def get_projects(self, slugs: List[str]) -> List[dict]:
        """Search for tags by hashtags."""
        response = self.call("project.search", {"constraints": {"slugs": slugs}})
        return response.get("data")

    def get_project_phid(self, slug: str) -> Optional[str]:
        projects = self.get_projects([slug])
        if not projects:
            return None

        return projects[0]["phid"]

    def ids_to_phids(self, rev_ids: List[int]) -> List[str]:
        """Convert revision ids to PHIDs.

        Parameters:
            rev_ids (list): A list of revision ids

        Returns:
            A list of PHIDs.
        """
        return [r["phid"] for r in self.get_revisions(ids=rev_ids)]

    def id_to_phid(self, rev_id: int) -> str:
        """Convert revision id to PHID."""
        phids = self.ids_to_phids([rev_id])
        if phids:
            return phids[0]

        raise NotFoundError("revision {} not found".format(rev_id))

    def phids_to_ids(self, phids: List[str]) -> List[str]:
        """Convert revision PHIDs to ids.

        Parameteres:
            phids (list): A list of PHIDs

        Returns:
            A list of ids.
        """
        return ["D{}".format(r["id"]) for r in self.get_revisions(phids=phids)]

    def phid_to_id(self, phid: str) -> str:
        """Convert revision PHID to id."""
        ids = self.phids_to_ids([phid])
        if ids:
            return ids[0]

        raise NotFoundError("revision {} not found".format(phid))

    def get_revisions(
        self, ids: Optional[List[int]] = None, phids: Optional[List[int]] = None
    ) -> List[dict]:
        """Get revisions info from Phabricator.

        Args:
            ids - list of revision ids
            phids - list of revision phids

        Returns a list of revisions ordered by ids or phids
        """
        if (ids and phids) or (ids is None and phids is None):
            raise ValueError("Internal Error: Invalid args to get_revisions")

        # Initialise depending on if we're passed revision IDs or PHIDs.
        if ids:
            ids = [str(rev_id) for rev_id in ids]
            phids_by_id = {
                rev_id: cache.get("rev-id-%s" % rev_id)
                for rev_id in ids
                if "rev-id-%s" % rev_id in cache
            }
            found_phids = list(phids_by_id.values())
            query_field = "ids"
            query_values = [
                int(rev_id) for rev_id in set(ids) - set(phids_by_id.keys())
            ]

        else:
            phids_by_id = {}
            found_phids = phids.copy()
            query_field = "phids"
            query_values = {phid for phid in phids if "rev-%s" % phid not in cache}

        # Revisions metadata keyed by PHID.
        revisions = {
            phid: cache.get("rev-%s" % phid)
            for phid in found_phids
            if "rev-%s" % phid in cache
        }

        # Query Phabricator if we don't have cached values for revisions.
        if query_values:
            api_call_args = {
                "constraints": {query_field: sorted(query_values)},
                "attachments": {"reviewers": True},
            }
            response = self.call("differential.revision.search", api_call_args)
            rev_list = response.get("data")

            for r in rev_list:
                phids_by_id[str(r["id"])] = r["phid"]
                revisions[r["phid"]] = r
                cache.set("rev-id-%s" % r["id"], r["phid"])
                cache.set("rev-%s" % r["phid"], r)

        # Return revisions in the same order requested.
        if ids:
            # Skip revisions for which we do not have a query result.
            return [
                revisions[phids_by_id[rev_id]]
                for rev_id in ids
                if rev_id in phids_by_id
            ]
        else:
            return [revisions[phid] for phid in phids]

    def get_diffs(
        self, ids: Optional[List[int]] = None, phids: Optional[List[str]] = None
    ) -> Dict[str, Dict]:
        """Get diffs from Phabricator.

        Args:
            ids - a list of diff IDs to pull
            phids - a list of diff PHIDs to pull

        Returns a dict of diffs identified by their PHID
        """
        if (ids and phids) or (ids is None and phids is None):
            raise ValueError("Internal Error: Invalid args to get_diffs")

        if ids:
            constraints = {"ids": list(set(ids))}
        else:
            constraints = {"phids": list(set(phids))}

        api_call_args = {
            "constraints": constraints,
            "attachments": {"commits": True},
        }
        response = self.call("differential.diff.search", api_call_args)
        diff_list = response.get("data", [])

        diff_dict = {}
        for d in diff_list:
            diff_dict[d["phid"]] = d

        return diff_dict

    def get_successor_phids(
        self, phid: str, include_abandoned: bool = False
    ) -> List[str]:
        return self.get_related_phids(
            phid, relation="child", include_abandoned=include_abandoned
        )

    def get_ancestor_phids(
        self, phid: str, include_abandoned: bool = False
    ) -> List[str]:
        return self.get_related_phids(
            phid, relation="parent", include_abandoned=include_abandoned
        )

    def get_related_phids(
        self, base_phid: str, relation: str = "parent", include_abandoned: bool = False
    ) -> List[str]:
        """Returns the list of PHIDs with direct dependency"""
        result = []

        def _get_related(phid):
            api_call_args = {"sourcePHIDs": [phid], "types": ["revision.%s" % relation]}
            edge = self.call("edge.search", api_call_args)
            if edge.get("data"):
                if len(edge["data"]) > 1:
                    raise NonLinearException()

                result.append(edge["data"][0]["destinationPHID"])
                _get_related(result[-1])

        _get_related(base_phid)

        if not result or include_abandoned:
            return result

        revisions = self.get_revisions(phids=result)
        return [
            r["phid"]
            for r in revisions
            if r["fields"]["status"]["value"] != "abandoned"
        ]

    def get_users(self, usernames: List[str]) -> List[dict]:
        """Get users using the user.query API.

        Caches the result in the process.
        Returns a list of existing Phabricator users data.
        """
        to_collect = []
        users = []
        for user in usernames:
            u = user.rstrip("!")
            key = "user-%s" % u
            if key in cache:
                users.append(cache.get(key))
            else:
                to_collect.append(u)

        if not to_collect:
            return users

        api_call_args = {"usernames": to_collect}
        # We're using the deprecated user.query API as the user.search does not
        # provide the user availability information.
        # See https://phabricator.services.mozilla.com/conduit/method/user.query/
        response = self.call("user.query", api_call_args)
        for user in response:
            users.append(user)
            key = "user-%s" % user["userName"]
            cache.set(key, user)
            cache.set(user["phid"], key)

        return users

    def get_groups(self, slugs: List[str]) -> List[dict]:
        to_collect = []
        groups = []
        for slug in slugs:
            s = slug.rstrip("!")
            key = "group-%s" % s
            if key in cache:
                groups.append(cache.get(key))
            else:
                to_collect.append(s)

        if not to_collect:
            return groups

        # See https://phabricator.services.mozilla.com/conduit/method/project.search/
        api_call_args = {"queryKey": "active", "constraints": {"slugs": to_collect}}
        response = self.call("project.search", api_call_args)
        for data in response.get("data"):
            group = {"name": data["fields"]["slug"], "phid": data["phid"]}
            groups.append(group)
            key = "group-%s" % group["name"]
            cache.set(key, group)

        # projects might be received by an alias.
        maps = response["maps"]["slugMap"]
        for alias in maps.keys():
            name = normalise_reviewer(alias)
            group = {"name": name, "phid": maps[alias]["projectPHID"]}
            key = "group-%s" % alias
            if key not in cache:
                groups.append(group)
                cache.set(key, group)

        return groups

    def create_revision(
        self,
        commit: Commit,
        diff_phid: str,
        parent_rev_phid: Optional[str] = None,
    ) -> dict:
        """Create a new revision in Phabricator."""
        transactions = [
            {"type": "title", "value": revision_title_from_commit(commit)},
            {"type": "summary", "value": commit.body},
        ]
        if commit.has_reviewers and not commit.wip:
            self.update_revision_reviewers(transactions, commit)

        if commit.bug_id:
            transactions.append({"type": "bugzilla.bug-id", "value": commit.bug_id})

        return self.edit_revision(
            transactions=transactions,
            diff_phid=diff_phid,
            wip=commit.wip,
            parent_rev_phid=parent_rev_phid,
        )

    def update_revision(
        self,
        commit: Commit,
        has_existing_reviewers: bool,
        diff_phid: Optional[str] = None,
        comment: Optional[str] = None,
        parent_rev_phid: Optional[str] = None,
    ) -> dict:
        """Update an existing revision in Phabricator."""
        # Update the title and summary
        transactions = [
            {"type": "title", "value": revision_title_from_commit(commit)},
            {"type": "summary", "value": strip_differential_revision(commit.body)},
        ]

        # Add update comment
        if comment:
            transactions.append({"type": "comment", "value": comment})

        # Add reviewers only if revision lacks them
        if commit.has_reviewers and not commit.wip:
            if not has_existing_reviewers:
                self.update_revision_reviewers(transactions, commit)

        # Update bug id if different
        if commit.bug_id:
            revision = conduit.get_revisions(ids=[commit.rev_id])[0]
            if revision["fields"]["bugzilla.bug-id"] != commit.bug_id:
                transactions.append({"type": "bugzilla.bug-id", "value": commit.bug_id})

        return self.edit_revision(
            transactions=transactions,
            diff_phid=diff_phid,
            rev_id=commit.rev_id,
            wip=commit.wip,
            parent_rev_phid=parent_rev_phid,
        )

    def edit_revision(
        self,
        transactions: Optional[List[dict]] = None,
        diff_phid: Optional[str] = None,
        rev_id: Optional[str] = None,
        wip: bool = False,
        parent_rev_phid: Optional[str] = None,
    ) -> dict:
        """Edit (create or update) a revision."""
        trans = transactions or []
        post_trans = []

        # diff_phid is not present for changes in revision settings (like WIP)
        if diff_phid:
            trans.append({"type": "update", "value": diff_phid})

        # Add revision parents.
        if parent_rev_phid:
            trans.append({"type": "parents.add", "value": [parent_rev_phid]})

        existing_status = None
        if rev_id:
            try:
                args = {"ids": [int(rev_id)]}
            except ValueError:
                args = {"phids": [rev_id]}
            existing_revision = conduit.get_revisions(**args)[0]
            existing_status = existing_revision["fields"]["status"]["value"]

        # Set revision for changes-planned or needs-review as required.
        # Phabricator will throw an error if we attempt to set a status to the same
        # as the current status.
        if wip:
            # Phabricator will automatically set the revision to needs-review
            # after the call to differential.revision.edit. If we are a creating a new
            # revision, we can add the `plan-changes` transaction to our create call.
            # Existing revisions must make a subsequent API call to set the status to
            # `changes-planned` to match our WIP state.
            plan_changes_transaction = {"type": "plan-changes", "value": True}
            if existing_status == "changes-planned":
                post_trans.append(plan_changes_transaction)
            else:
                trans.append(plan_changes_transaction)

        # Let the Phabricator sort out the correct status for new revisions to avoid
        # emails being sent before phabbugs has processed the revision.
        # Updates to existing reviews that have been accepted shouldn't be triggered
        # for re-review.
        elif existing_status and existing_status not in ("needs-review", "accepted"):
            trans.append({"type": "request-review", "value": True})

        # Call differential.revision.edit
        api_call_args = {"transactions": trans}
        if rev_id:
            api_call_args["objectIdentifier"] = rev_id
        revision = self.call("differential.revision.edit", api_call_args)
        if not revision:
            raise ConduitAPIError("Can't edit the revision.")

        # Run post-edit transactions if required.
        if post_trans:
            revision = self.call(
                "differential.revision.edit",
                {
                    "objectIdentifier": rev_id,
                    "transactions": post_trans,
                },
            )

        return revision

    def apply_transactions_to_revision(self, rev_id: str, transactions: List[dict]):
        """Apply transactions to the specified revision."""
        self.call(
            "differential.revision.edit",
            {"objectIdentifier": rev_id, "transactions": transactions},
        )

    def get_repository_by_callsign(self, call_sign: str) -> dict:
        """Get repository info for a repo on Phabricator by callsign."""
        return self.repository_search_single("callsigns", call_sign)

    def get_repository_by_shortname(self, short_name: str) -> dict:
        """Get repository info for a repo on Phabricator by shortname."""
        return self.repository_search_single("shortNames", short_name)

    def repository_search_single(self, constraint: str, value: str) -> dict:
        """Get the information about a single repository from Phabricator.

        Takes a `constraint` to be passed to `diffusion.repository.search`
        and a `value` to be passed as the value of `constraint`. `value`
        must be a single element and will be passed in a list to Conduit.
        """
        api_call_args = {"constraints": {constraint: [value]}, "limit": 1}
        data = self.call("diffusion.repository.search", api_call_args)
        if not data.get("data"):
            raise NotFoundError("Repository %s not found" % value)

        repo = data["data"][0]
        return repo

    def get_repositories_with_tag(self, tag: str) -> dict:
        """Get repository information for repos associated with the given tag."""
        api_call_args = {
            "constraints": {
                "projects": [tag],
            }
        }

        data = self.call("diffusion.repository.search", api_call_args).get("data")
        if not data:
            raise NotFoundError(f"No repositories found with tag {tag}")

        return data

    def submit_diff(self, diff: Diff, commit: Commit) -> dict:
        files_changed = sorted(
            diff.changes.values(), key=operator.attrgetter("cur_path")
        )
        changes = [
            change.to_conduit(conduit.repo.get_public_node(commit.node))
            for change in files_changed
        ]

        base_revision = conduit.repo.get_public_node(commit.parent)

        creation_method = ["moz-phab", conduit.repo.vcs]
        if conduit.repo.vcs == "git" and conduit.repo.is_cinnabar_required:
            creation_method.append("cinnabar")

        # Use the repo of the existing revision if this is an update.
        revs = conduit.get_revisions(ids=[commit.rev_id]) if commit.rev_id else []
        diff_repo_phid = revs[0]["fields"]["repositoryPHID"] if revs else self.repo_phid

        api_call_args = {
            "changes": changes,
            "sourceMachine": self.repo.phab_url,
            "sourceControlSystem": self.repo.phab_vcs,
            "sourceControlPath": "/",
            "sourceControlBaseRevision": base_revision,
            "creationMethod": "-".join(creation_method),
            "lintStatus": "none",
            "unitStatus": "none",
            "repositoryPHID": diff_repo_phid,
            "sourcePath": self.repo.path,
            "branch": "HEAD" if self.repo.phab_vcs == "git" else "default",
        }
        return self.call("differential.creatediff", api_call_args)

    def set_diff_property(self, diff_id: str, commit: Commit, message: str):
        """Add information about our local commit to the diff."""
        data = {
            commit.node: {
                "author": commit.author_name,
                "authorEmail": commit.author_email,
                "time": commit.author_date_epoch,
                "summary": revision_title_from_commit(commit),
                "message": message,
                "commit": conduit.repo.get_public_node(commit.node),
                "parents": [conduit.repo.get_public_node(commit.parent)],
            }
        }
        if commit.tree_hash is not None:
            data[commit.node]["tree"] = commit.tree_hash

        if self.repo.phab_vcs == "hg":
            data[commit.node]["rev"] = commit.node

        api_call_args = {
            "diff_id": diff_id,
            "name": "local:commits",
            "data": json.dumps(data),
        }
        self.call("differential.setdiffproperty", api_call_args)

    def upload_files_from_diff(self, diff: Diff):
        # Files are uploaded in parallel using a pool of threads.
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for change in diff.changes.values():
                for upload in change.uploads:
                    path = (
                        change.cur_path if upload["type"] == "new" else change.old_path
                    )
                    futures.append(executor.submit(self.upload_file, upload, path))

            # Wait for all uploads to be finished.
            concurrent.futures.wait(futures)

            # Check that all went well. If not, propagate the first error here
            # by calling the future's result() method.
            for upload in futures:
                upload.result()

    def upload_file(self, upload: dict, path: str):
        data: bytes = upload["value"]
        if not data:
            return
        name = os.path.basename(path)
        allocation = self.call(
            "file.allocate",
            {
                "name": name,
                "contentLength": len(data),
                "contentHash": hashlib.sha256(data).hexdigest(),
            },
        )
        file_phid = allocation["filePHID"]
        if allocation["upload"]:
            if not file_phid:
                data_base64 = base64.standard_b64encode(data)
                file_phid = self.call(
                    "file.upload", {"data_base64": data_base64.decode(), "name": name}
                )
            else:
                chunks = self.call("file.querychunks", {"filePHID": file_phid})
                for chunk in chunks:
                    if chunk["complete"]:
                        continue
                    byte_start = int(chunk["byteStart"])
                    byte_end = int(chunk["byteEnd"])
                    data_base64 = base64.standard_b64encode(data[byte_start:byte_end])
                    self.call(
                        "file.uploadchunk",
                        {
                            "filePHID": file_phid,
                            "byteStart": byte_start,
                            "data": data_base64.decode(),
                            "dataEncoding": "base64",
                        },
                    )

        upload["phid"] = str(file_phid)

    def whoami(self, *, api_token: Optional[str] = None) -> dict:
        if "whoami" in cache:
            return dict(cache.get("whoami"))

        who = self.call("user.whoami", {}, api_token=api_token)
        cache.set("whoami", who)
        return who

    def update_revision_reviewers(
        self, transactions: List[Dict[str, Any]], commit: Commit
    ):
        # Appends differential.revision.edit transaction(s) to `transactions` to
        # set the reviewers.

        all_reviewing = commit.reviewers["request"] + commit.reviewers["granted"]

        # Find reviewers PHIDs
        all_reviewers = [r for r in all_reviewing if not r.startswith("#")]
        # preload all reviewers
        self.get_users(all_reviewers)
        reviewers = [r for r in all_reviewers if not r.endswith("!")]
        blocking_reviewers = [r.rstrip("!") for r in all_reviewers if r.endswith("!")]
        reviewers_phid = [user["phid"] for user in self.get_users(reviewers)]
        blocking_phid = [
            "blocking(%s)" % user["phid"] for user in self.get_users(blocking_reviewers)
        ]

        # Find groups PHIDs
        all_groups = [g for g in all_reviewing if g.startswith("#")]
        groups = [g for g in all_groups if not g.endswith("!")]
        blocking_groups = [g.rstrip("!") for g in all_groups if g.endswith("!")]
        # preload all groups
        self.get_groups(all_groups)
        groups_phid = [group["phid"] for group in self.get_groups(groups)]
        bl_groups_phid = [
            "blocking(%s)" % group["phid"] for group in self.get_groups(blocking_groups)
        ]

        all_reviewing_phid = (
            reviewers_phid + blocking_phid + groups_phid + bl_groups_phid
        )
        transactions.extend([{"type": "reviewers.set", "value": all_reviewing_phid}])

    def check_for_invalid_reviewers(self, reviewers: dict) -> List[Dict[str, Any]]:
        """Return a list of invalid reviewer names.

        Args:
            reviewers: A commit reviewers dict of granted and requested reviewers.
        """

        # Combine the lists of requested reviewers and granted reviewers.
        all_reviewers = []
        found_names = []
        for sublist in list(reviewers.values()):
            all_reviewers.extend(
                [
                    normalise_reviewer(r, strip_group=False)
                    for r in sublist
                    if not r.startswith("#")
                ]
            )

        users = []
        if all_reviewers:
            users = self.get_users(all_reviewers)
            found_names = [
                normalise_reviewer(data["userName"], strip_group=False)
                for data in users
            ]

        # Group reviewers are represented by a "#" prefix
        all_groups = []
        found_groups = []
        for sublist in list(reviewers.values()):
            all_groups.extend(
                [
                    normalise_reviewer(r, strip_group=False)
                    for r in sublist
                    if r.startswith("#")
                ]
            )

        if all_groups:
            groups = self.get_groups(all_groups)
            found_groups = [
                "#%s" % normalise_reviewer(group["name"]) for group in groups
            ]

        all_reviewers.extend(all_groups)
        found_names.extend(found_groups)
        invalid = list(set(all_reviewers) - set(found_names))

        # Find users availability:
        unavailable = [
            {
                "name": r["userName"],
                "until": datetime.datetime.fromtimestamp(
                    r["currentStatusUntil"]
                ).strftime("%Y-%m-%d %H:%M"),
            }
            for r in users
            if r.get("currentStatus") == "away"
        ]

        # Find disabled users:
        disabled = [
            {"name": r["userName"], "disabled": True}
            for r in users
            if "disabled" in r.get("roles", [])
        ]
        return disabled + unavailable + [{"name": r} for r in invalid]


conduit = ConduitAPI()
