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
command_path =

[hg]
command_path =

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
self_auto_update = True
get_pre_releases = False

[error_reporting]
report_to_sentry = True
```

- `ui.no_ansi` : Never use ANSI colours (default: auto-detected).
- `vcs.safe_mode` : Use only safe VCS settings (default: `False`). Use `--safe-mode`
    option to switch it on for a one-time usage.
- `git.remote`: Comma separated string. Default remotes used to find the first
    unpublished commit. Default, empty string, means that a list of remotes will
    be read from `git remote` command.
- `git.command_path`: Command path to Git binary.
- `hg.command_path`: Command path to Mercurial binary.
- `submit.auto_submit` : When `True` the confirmation prompt will be skipped (default:
    `False`).
- `submit.always_blocking` : When `True` reviewers in commit descriptions will be
    marked as blocking. reviewers specified on the command line override this setting
    (default: `False`).
- `submit.warn_untracked` : When `True` show a warning if there are uncommitted or
    untracked changes in the working directory (default: `True`).
- `patch.apply_to` : [base/here] Where to apply the patches by default. If `"base"`
    `moz-phab` will look for the SHA1 in the first commit. If `"here"` - current
    commit/checkout will be used (default: base).
- `patch.create_bookmark` : Affects only when patching a Mercurial repository. If
    `True` moz-phab will create a bookmark (based on the last revision number) for the
    new DAG branch point.
- `patch.always_full_stack` : When `False` and the patched revision has successors,
    moz-phab will ask if the whole stack should be patched instead. If `True`
    moz-phab will do it without without asking.
- `updater.self_last_check` : Epoch timestamp (local timezone) indicating the last time
    an update check was performed for this script.  set to `-1` to disable this check.
- `self_auto_update` : When `True` moz-phab will auto-update if a new version is
    available. If `False` moz-phab will only warn about the new version.
- `get_pre_releases` : When `True` moz-phab auto-update will fetch pre-releases if they
    are available, otherwise pre-releases will be ignored (default: `False`).
- `error_reporting.report_to_sentry` : When `True` moz-phab will submit exceptions to
    Sentry so moz-phab devs can see unreported errors.

`moz-phab` can also be configured via the following environmental variables:
- `DEBUG` : Enabled debugging output (default: disabled).

## Execution

To get information about all available commands run
```
  $ moz-phab -h
```

All commands involving VCS (like `submit` and `patch`) might be used with a
`--safe-mode` switch. It will run the VCS command with only chosen set of extensions.

### Submitting commits to Phabricator
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


### Downloading a patch from Phabricator

`moz-phab patch` allows patching an entire stack of revisions. The simplest
invocation is

```
  $ moz-phab patch revision_id
```

To patch a stack ending with the revision `D123` run `moz-phab patch D123`.
Diffs will be downloaded from Phabricator and applied using the underlying
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
   `$ moz-phab patch D123 --raw | hg import`.
   `$ moz-phab patch D123 --raw | git am`.

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

### Associating a commit to an existing phabricator revision

`moz-phab` tracks which revision is associated with a commit using a line in the
commit message. If you want to work on an existing revision from a different
machine or environment, we recommend you [apply the existing revision from
Phabricator using `moz-phab patch`](#downloading-a-patch-from-phabricator).

If that isn't an option for whatever reason, you can associate a new commit to
the same revision by adding a line similar to the following to the extended
commit message:

```
Differential Revision: https://phabricator.services.mozilla.com/D[revision]
```

replacing `[revision]` with the identifier of your revision.

## Reporting Issues

We use [Bugzilla](https://bugzilla.mozilla.org/) to track development.

File bugs in Bugzilla under
[Conduit :: moz-phab](https://bugzilla.mozilla.org/enter_bug.cgi?product=Conduit&component=moz-phab).

## Development

All python code must be formatted with [black](https://github.com/ambv/black)
using the default settings.

### MacOS / Linux

1. Ensure you have Python 3, Git, and Mercurial installed
   - eg. using `homebrew` on macOS, or your Linux distribution's package manager
   - `python3`, `git`, and `hg` executables must be on the system path
2. In your clone of this repository run the following commands (adjusting to the version of Python):
   - `python3 -m venv venv`
   - `venv/bin/pip3 install -r dev/requirements/python3.9.txt`
   - `venv/bin/pip3 install -e .`
3. To run moz-phab after making modifications use `venv/bin/moz-phab-dev`
4. To run tests use `venv/bin/pytest -vv`

### Windows

1. Install Python 3, Git, and Mercurial:
   - Run `python3` from the command prompt and install from the Windows store.
   - Install Git and Mercurial with their respective installers from the
     official websites.
   - `python3`, `git`, and `hg` executables must be on the system path
2. In your clone of this repository run the following commands:
   - `python3 -m venv venv`
   - `venv\Scripts\pip3 install -r dev-requirements.txt`
   - `venv\Scripts\pip3 install -e .`
3. To run moz-phab after making modifications use `venv\Scripts\moz-phab-dev`
4. To run tests use `venv\Scripts\pytest -vv`

### Regenerating requirements files

Requirements files (those found in the `dev/requirements` directory) are automatically
generated using pip-tools. These requirement files are used in the CircleCI
configuration to install requirements that run remotely on CircleCI. You can use Docker
to regenerate these files.

#### On Linux

To generate `dev/requirements/python*.*.txt`, run the following commands while in the
`dev` directory:

- `docker-compose run generate-python3.6-requirements`
- `docker-compose run generate-python3.7-requirements`
- `docker-compose run generate-python3.8-requirements`
- `docker-compose run generate-python3.9-requirements`

#### On Windows

To generate `dev/requirements/windows.txt`, make sure you are not running Docker in WSL
mode then run the following command:

- `docker-compose run generate-windows-requirements`

### Circle CI

`mozphab` uses Circle CI to ensure all tests pass on macOS, Linux, and Windows.

To ensure that your changes work, run `circleci` locally.

1. Ensure you have the `circleci` client installed, see https://circleci.com/docs/2.0/local-cli/
2. In your clone of this repository, run:
   `circleci local execute --job test_3_8`

This will run all the Python 3.8 tests in a dockerized environment.
This step takes a while, so you might want to run `pytest` for working on your changes,
as explained above.

#### Circle CI on Windows

As of the time of writing, `circleci-cli` on Windows does not allow you to execute
Windows tests locally. When CircleCI is running your windows tests remotely, it will
use a Windows Orb that is configured to use a special Windows executor that is preloaded
with various development packages. The Windows virtual machine will use Miniconda to
bootstrap the Python environment, which can cause some problems when installing
additional requirements. The `generate-windows` container that is used to generate
requirements files for Windows can be used to run your tests, as well as to test package
installation. To do that, run the following commands:

- `docker-compose run generate-windows powershell.exe`
- `cd C:\review`
- `pip install dev\requirements\windows.txt`
- `pytest`

### Submitting patches

Pull Requests are not accepted here; please submit changes to Phabricator using `moz-phab`.

1. Follow the [setup](https://moz-conduit.readthedocs.io/en/latest/phabricator-user.html#setting-up-mozphab)
2. Once your patch is written and committed locally, run `moz-phab` to send it to Phabricator

### Local environment

By using [suite](https://github.com/mozilla-conduit/suite), you can run a local
environment with its own instances of Phabricator, BMO, Hg, and other services.

This enables more thorough integration testing of `moz-phab` without affecting
production data.

You can order the suite to use your local code by calling:

```
$ docker-compose -f docker-compose.yml -f docker-compose.review.yml run local-dev
````
