# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import operator
import re
import concurrent.futures

from .conduit import conduit


class Diff:
    """Representation of the Diff used to submit to the Phabricator."""

    class Hunk:
        def __init__(self, *, old_off, old_len, new_off, new_len, lines):
            """
            Hunk object, encapsulates hunk metadata and diff lines.

            For the following hunk:
            @@ -23,6 +23,7 @@ jobs:
             - run:
                 name: install dependencies
                 command: |
             +     set -e
                   # install modern hg
                   sudo pip3 install --disable-pip-version-check mercurial hg-evolve
                   # configure hg

            :param int old_off: old offset (eg. -23)
            :param int old_len: old length/line count (eg. 6)
            :param int new_off: new offset (eg. +23)
            :param int new_len: new length (eg. 7)
            :param list[str] lines: list of diff lines, starting with "+", "-", or " ",
                   including the trailing "\n".  (eg. the 7 lines following the @@ line)
            """
            self.old_off = old_off
            self.old_len = old_len
            self.new_off = new_off
            self.new_len = new_len
            self.corpus = "".join(lines)

            self.old_eof_newline = True
            self.new_eof_newline = True
            self.added = 0
            self.deleted = 0

            prev_line = " "
            for line in lines:
                if line[0] == "+":
                    self.added += 1
                elif line[0] == "-":
                    self.deleted += 1
                if line.endswith("No newline at end of file\n"):
                    if prev_line[0] != "+":
                        self.old_eof_newline = False
                    if prev_line[0] != "-":
                        self.new_eof_newline = False
                prev_line = line

    class Change:
        def __init__(self, path):
            self.old_mode = None
            self.cur_mode = None
            self.old_path = None
            self.cur_path = path
            self.away_paths = []
            self.kind = Diff.Kind("CHANGE")
            self.binary = False
            self.file_type = Diff.FileType("TEXT")
            self.uploads = []
            self.hunks = []

        @property
        def added(self):
            return sum(hunk.added for hunk in self.hunks)

        @property
        def deleted(self):
            return sum(hunk.deleted for hunk in self.hunks)

        def from_git_diff(self, git_diff):
            """Generate hunks from the provided git_diff output."""

            # Process each hunk
            hunk = None
            in_header = True
            for line in git_diff.splitlines(keepends=True):
                # Skip lines before the start of the first hunk header
                if in_header:
                    if not line.startswith("@@"):
                        continue
                    in_header = False

                # Start of hunk
                if line.startswith("@@"):
                    # Store previously collected hunk
                    if hunk and hunk["lines"]:
                        self.hunks.append(Diff.Hunk(**hunk))

                    # Start a new collection
                    (old_off, new_off, old_len, new_len) = Diff.parse_git_diff(line)
                    hunk = dict(
                        old_off=old_off,
                        new_off=new_off,
                        old_len=old_len,
                        new_len=new_len,
                        lines=[],
                    )

                else:
                    hunk["lines"].append(line)
            if hunk and hunk["lines"]:
                self.hunks.append(Diff.Hunk(**hunk))

        def set_as_binary(self, *, a_body, a_mime, b_body, b_mime):
            """Updates Change contents to the provided binary data."""
            self.binary = True

            self.uploads = [
                {"type": "old", "value": a_body, "mime": a_mime, "phid": None},
                {"type": "new", "value": b_body, "mime": b_mime, "phid": None},
            ]

            if a_mime.startswith("image/") or b_mime.startswith("image/"):
                self.file_type = Diff.FileType("IMAGE")
            else:
                self.file_type = Diff.FileType("BINARY")

        def to_conduit(self, node):
            # Record upload information
            metadata = {}
            for upload in self.uploads:
                metadata["%s:binary-phid" % upload["type"]] = upload["phid"]
                metadata["%s:file:size" % upload["type"]] = len(upload["value"])
                metadata["%s:file:mime-type" % upload["type"]] = upload["mime"]

            # Translate hunks
            hunks = [
                {
                    "oldOffset": hunk.old_off,
                    "oldLength": hunk.old_len,
                    "newOffset": hunk.new_off,
                    "newLength": hunk.new_len,
                    "addLines": hunk.added,
                    "delLines": hunk.deleted,
                    "isMissingOldNewline": not hunk.old_eof_newline,
                    "isMissingNewNewline": not hunk.new_eof_newline,
                    "corpus": hunk.corpus,
                }
                for hunk in self.hunks
            ]

            old_props = {"unix:filemode": self.old_mode} if self.old_mode else {}
            cur_props = {"unix:filemode": self.cur_mode} if self.cur_mode else {}

            return {
                "metadata": metadata,
                "oldPath": self.old_path,
                "currentPath": self.cur_path,
                "awayPaths": self.away_paths,
                "oldProperties": old_props,
                "newProperties": cur_props,
                "commitHash": node,
                "type": self.kind.value,
                "fileType": self.file_type.value,
                "hunks": hunks,
            }

    class Kind:
        values = dict(
            ADD=1,
            CHANGE=2,
            DELETE=3,
            MOVE_AWAY=4,
            COPY_AWAY=5,
            MOVE_HERE=6,
            COPY_HERE=7,
            MULTICOPY=8,
        )

        def __init__(self, name):
            self.value = self.values[name]
            self.name = name

        def short(self):
            if self.name == "ADD":
                return "A "
            elif self.name == "CHANGE":
                return "M "
            elif self.name == "DELETE":
                return "D "
            elif self.name == "MOVE_AWAY":
                return "R>"
            elif self.name == "MOVE_HERE":
                return ">R"
            elif self.name == "COPY_AWAY":
                return "C>"
            elif self.name == "COPY_HERE":
                return ">C"
            elif self.name == "MULTICOPY":
                return "C*"

    class FileType:
        values = dict(
            TEXT=1,
            IMAGE=2,
            BINARY=3,
            DIRECTORY=4,  # Should never show up...
            SYMLINK=5,  # Support symlinks (do we care?)
            DELETED=6,
            NORMAL=7,
        )

        def __init__(self, name):
            self.value = self.values[name]
            self.name = name

    def __init__(self):
        self.changes = {}
        self.phid = None
        self.id = None

    def change_for(self, path):
        if path not in self.changes:
            self.changes[path] = self.Change(path)
        return self.changes[path]

    def set_change_kind(self, change, kind, a_mode, b_mode, a_path, _b_path):
        """Determine the correct kind from the letter."""
        if kind == "A":
            change.kind = self.Kind("ADD")
            change.cur_mode = b_mode

        elif kind == "D":
            change.kind = self.Kind("DELETE")
            change.old_mode = a_mode
            change.old_path = a_path

        elif kind == "M":
            change.kind = self.Kind("CHANGE")
            if a_mode != b_mode:
                change.old_mode = a_mode
                change.cur_mode = b_mode
            change.old_path = a_path
            assert change.old_path == change.cur_path

        elif kind == "R":
            change.kind = self.Kind("MOVE_HERE")
            if a_mode != b_mode:
                change.old_mode = a_mode
                change.cur_mode = b_mode

            change.old_path = a_path
            old = self.change_for(change.old_path)
            if old.kind.name in ["MOVE_AWAY", "COPY_AWAY"]:
                old.kind = self.Kind("MULTICOPY")
            elif old.kind.name != "MULTICOPY":
                old.kind = self.Kind("MOVE_AWAY")

            old.away_paths.append(change.cur_path)

        elif kind == "C":
            change.kind = self.Kind("COPY_HERE")
            if a_mode != b_mode:
                change.old_mode = a_mode
                change.cur_mode = b_mode

            change.old_path = a_path
            old = self.change_for(change.old_path)
            if old.kind.name in ["MOVE_AWAY", "COPY_AWAY"]:
                old.kind = self.Kind("COPY_AWAY")
            elif old.kind.name != "MULTICOPY":
                old.kind = self.Kind("COPY_AWAY")

            old.away_paths.append(change.cur_path)

        else:
            raise "unsupported change type %s" % kind

    def _upload_file(self, change, upload):
        path = change.cur_path if upload["type"] == "new" else change.old_path
        upload["phid"] = conduit.file_upload(path, upload["value"])

    def upload_files(self):
        futures = []

        # files are uploaded in parallel, using a pool of threads.
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            for change in list(self.changes.values()):
                for upload in change.uploads:
                    futures.append(executor.submit(self._upload_file, change, upload))

            # wait for all uploads to be finished
            concurrent.futures.wait(futures)

            # check that all went well. If not, propagate the first error here
            # by calling the future's result() method
            for upload in futures:
                upload.result()

    def submit(self, commit, message):
        files_changed = sorted(
            self.changes.values(), key=operator.attrgetter("cur_path")
        )
        changes = [
            change.to_conduit(conduit.repo.get_public_node(commit["node"]))
            for change in files_changed
        ]
        diff = conduit.create_diff(
            changes, conduit.repo.get_public_node(commit["parent"])
        )

        self.phid = diff["phid"]
        self.id = diff["diffid"]
        self.set_property(commit, message)
        return diff["phid"]

    def set_property(self, commit, message):
        """Add information about our local commit to the patch."""
        conduit.set_diff_property(self.id, commit, message)

    @staticmethod
    def parse_git_diff(hdr):
        m = re.match(
            r"@@ -(?P<old_off>\d+)(?:,(?P<old_len>\d+))? "
            r"\+(?P<new_off>\d+)(?:,(?P<new_len>\d+))? @@",
            hdr,
        )
        old_off = int(m.group("old_off"))
        old_len = int(m.group("old_len") or 1)
        new_off = int(m.group("new_off"))
        new_len = int(m.group("new_len") or 1)
        return old_off, new_off, old_len, new_len
