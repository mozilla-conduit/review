# Phabricator CLI from Mozilla to support submission of a series of commits.

## Installation

`moz-phab` can be installed with `pip3 install MozPhab`.

For detailed installation instructions please see:

- [Windows Install Instructions](https://moz-conduit.readthedocs.io/en/latest/mozphab-windows.html)
- [Linux Install Instructions](https://moz-conduit.readthedocs.io/en/latest/mozphab-linux.html)
- [macOS Install Instructions](https://moz-conduit.readthedocs.io/en/latest/mozphab-macos.html)

`moz-phab` will periodically check for updates and seamlessly install the latest release
when available. To force update `moz-phab`, run `moz-phab self-update`.

### Changelog

https://wiki.mozilla.org/MozPhab#Changelog

## Configuration

`moz-phab` has an INI style configuration file to control defaults: `~/.moz-phab-config`

This file will be created if it doesn't exist.

```
[ui]
no_ansi = False

[vcs]
safe_mode = False

[git]
remote =

[submit]
auto_submit = False
always_blocking = False
warn_untracked = True

[patch]
apply_to = base
create_bookmark = True
always_full_stack = False

[updater]
self_last_check = 0
arc_last_check = 0
self_auto_update = True
```

- `ui.no_ansi` : never use ANSI colours (default: auto-detected).
- `vcs.safe_mode` : use only safe VCS settings (default: false). Use `--safe-mode`
    option to switch it on for a one-time usage.
- `git.remote`: comma separated string. Default remotes used to find the first
    unpublished commit. Default, empty string, means that a list of remotes will
    be read from `git remote` command.
- `submit.auto_submit` : when true the confirmation prompt will be skipped
    (default: false).
- `submit.always_blocking` : when true reviewers in commit descriptions will be marked
    as blocking. reviewers specified on the command line override this setting
    (default: false).
- `submit.warn_untracked` : when true show a warning if there are uncommitted or
    untracked changes in the working directory (default: true)
- `patch.apply_to` : [base/here] Where to apply the patches by default. If `"base"`
    `moz-phab` will look for the SHA1 in the first commit. If `"here"` - current
    commit/checkout will be used (default: base).
- `patch.create_bookmark` : affects only when patching a Mercurial repository. If `True`
    `moz-phab` will create a bookmark (based on the last revision number) for the
    new DAG branch point.
- `patch.always_full_stack` : when `False` and the patched revision has successors,
    moz-phab will ask if the whole stack should be patched instead. If `True`
    moz-phab will do it without without asking.
- `updater.self_last_check` : epoch timestamp (local timezone) indicating the last time
    an update check was performed for this script.  set to `-1` to disable this check.
- `updater.arc_last_check` : epoch timestamp (local timezone) indicating the last time
    an update was performed for arc.  set to `-1` to disable this check.
- `self_auto_update` : when `True` moz-phab will auto-update if a new version is available.
    If `False` moz-phab will only warn about the new version.

`moz-phab` can also be configured via the following environmental variables:
- `DEBUG` : enabled debugging output (default: disabled)
- `UPDATE_FILE` : when self-updating write to this file instead of \_\_file\_\_

## Execution

To get information about all available commands run
```
  $ moz-phab -h
```

All commands involving VCS (like `submit` and `patch`) might be used with a
`--safe-mode` switch. It will run the VCS command with only chosen set of extensions.

### Submitting commits to the Phabricator
The simplest invocation is

```
  $ moz-phab [start_rev] [end_rev]
```

If no positional arguments (`start_rev`/`end_rev`) are given, the
range of commits is automatically determined, starting with the first
non-public, non-obsolete changeset (for Mercurial) or first unpublished commit
(for Git) and ending with the currently checked-out changeset. If at least one
argument is given `moz-phab` is following the underlying VCS's `log` behavior.
The first argument is interpreted differently in Mercurial (as inclusive) and
Git (exclusive). If only one argument is given the end of range is again
interpreted as the currently checked-out changeset.  If both arguments are
given - the second one is interpreted as inclusive.

Bug IDs and reviewers are parsed out of commit messages by default.
You can set a reviewer as blocking by appending an exclamation mark to
the reviewer's nick, e.g.  `r=foo!`.  If `submit.always_blocking` is
set to `true` (see above), reviewers will always be set to blocking
regardless.

A bug ID can also be set *for every revision in the series* with the
`--bug` option, which overrides any bug IDs in commit messages.
Similarly, reviewers can be set *for every revision in the series*
with `--reviewer` (regular reviewers) and/or `--blocker` (blocking
reviewers), which again overrides any reviewers in commit messages.

Run `moz-phab submit -h` for more options for submitting revisions.

To submit updates to a commit series, run `moz-phab` in the same way
with the same arguments, that is, specifying the full original range
of commits.  Note that, while inserting and amending commits should
work fine, reordering commits is not yet supported, and deleting
commits will leave the associated revisions open, which should be
abandoned manually.  See
[bug 1481539](https://bugzilla.mozilla.org/show_bug.cgi?id=1481539) for
planned fixes.  Also note that "fix-up" commits are not yet supported;
see [bug 1481542](https://bugzilla.mozilla.org/show_bug.cgi?id=1481542).

MozPhab is not using Arcanist to submit commits to the Phabricator.
If you wish to do so add the `--arc` switch. File a bug if you needed to use it because
MozPhab failed to submit with default settings.

### Downloading a patch from the Phabricator

`moz-phab patch` allows patching an entire stack of revisions. The simplest
invocation is

```
  $ moz-phab patch revision_id
```

To patch a stack ending with the revision `D123` run `moz-phab patch D123`.
Diffs will be downloaded from the Phabricator and applied using the underlying
VCS (`import` for Mercurial or `apply` for Git). A commit for each revision will
be created in a new bookmark (Mercurial) or branch (Git).

This behavior can be modified with few options:

- `--apply-to TARGET` Define the commit to which apply the patch:
  - `base` (default) find the base commit in the first ancestor of the revision,
  - `here` use the current commit,
  - `{NODE}` use a commit identified by SHA1 or (in Mecurial) revision number

- `--raw` Print out the diffs of each revision starting from the oldest
   ancestor instead of applying to the repository. It can be used to patch the
   working directory with an external tool:
   `$ moz-phab patch D123 --raw | patch -p1`.

- `--no-commit` Use the `git apply` command (also for Mercurial repos) to patch
   the diffs. No commit or branch is created.

- `--no-bookmark` : used only when patching a Mercurial repository. If not
    provided - `moz-phab` will create a bookmark (based on the last revision number)
    for the new DAG branch point. The default behavior [is configurable](#configuration).

- `--no-branch`: used only when patching a Git repository. If not provided -
    `moz-phab` will create a branch (based on the revision number). Otherwise
    commits will be added just on top of the *base commit* which might result
    in switching the repository to the 'detached HEAD' state.

- `--skip-dependencies` : patch only one revision, ignore dependencies.

### Reorganizing the stack

`moz-phab reorg [start_rev] [end_rev]` allows you to reorganize the stack in Phabricator.

If you've changed the local stack by adding, removing or moving the commits around,
you need to change the parent/child relation of the revisions in Phabricator.

`moz-phab reorg` command will compare the stack, display what will be changed and 
ask for permission before taking any action.

### Running arc commands

* **Note** You will need to have PHP installed to run `arc` commands.*

`moz-phab arc` allows running Arcanist commands indirectly:

```
$ moz-phab arc ARG [ARG ...]
```

`arc feature` will become `moz-phab arc feature`.

## Reporting Issues

We use [Bugzilla](https://bugzilla.mozilla.org/) to track development.

File bugs in Bugzilla under
[Conduit :: moz-phab](https://bugzilla.mozilla.org/enter_bug.cgi?product=Conduit&component=moz-phab).

## Development

To install your code call `pip3 install -e .` from MozPhab project directory (`review`
by default). Your code will be executed by calling `moz-phab`.

Tests can be executed with `pytest`.
Integration tests require to have access to `git`, `hg` with `evolve` extension.

All python code must be formatted with [black](https://github.com/ambv/black)
using the default settings.

Pull Requests are not accepted here; please submit changes with Phabricator.

Consider using [suite](https://github.com/mozilla-conduit/suite) to use MozPhab with 
Phabricator installed on your local machine.

You can order the suite to use your local code by calling:

```
$ docker-compose -f docker-compose.yml -f docker-compose.review.yml run local-dev`
````
