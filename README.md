# moz-phab

Phabricator CLI from Mozilla to support submission of a series of commits.

## Installation

`moz-phab` can be installed with `pip3 install MozPhab`.

For detailed installation instructions please see:

- [Windows Install Instructions](https://moz-conduit.readthedocs.io/en/latest/mozphab-windows.html)
- [Linux Install Instructions](https://moz-conduit.readthedocs.io/en/latest/mozphab-linux.html)
- [macOS Install Instructions](https://moz-conduit.readthedocs.io/en/latest/mozphab-macos.html)

`moz-phab` will periodically check for updates and seamlessly install the latest
release when available. To force update `moz-phab`, run `moz-phab self-update`.

### Changelog

Changelog is available on the [MozPhab page on Mozilla Wiki](https://wiki.mozilla.org/MozPhab/Changelog).

## Configuration

`moz-phab` has an INI style configuration file to control defaults: `~/.moz-phab-config`

This file will be created if it doesn't exist.

```ini
[ui]
no_ansi = False
hyperlinks = True

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
ai_review = False

[patch]
apply_to = base
create_bookmark = True
create_topic = False
create_branch = True
always_full_stack = False
branch_name_template = phab-D{rev_id}
create_commit = True

[updater]
self_last_check = 0
self_auto_update = True
get_pre_releases = False

[error_reporting]
report_to_sentry = True
```

- `ui.no_ansi` : Never use ANSI colours (default: auto-detected).
- `ui.hyperlinks` : Enable terminal hyperlinks for revision IDs and bug numbers (default: `True`).
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
- `submit.ai_review` : When `True` moz-phab will automatically request an AI review
    for newly created revisions. AI review is not requested for updates to existing
    revisions; use the `--ai` flag to explicitly request AI review on updates
    (default: `False`).
- `patch.apply_to` : [base/here] Where to apply the patches by default. If `"base"`
    `moz-phab` will look for the SHA1 in the first commit. If `"here"` - current
    commit/checkout will be used (default: base).
- `patch.create_bookmark` : Affects only when patching a Mercurial repository. If
    `True` moz-phab will create a bookmark (based on the last revision number)
    for the new DAG branch point.
- `patch.create_topic` : Affects only when patching a Mercurial repository.
    Requires the `topic` extension to be enabled. If `True` moz-phab will
    create a topic (based on the last revision number) for the new DAG branch
    point.
- `patch.create_branch` : Affects only when patching a Git repository. If
    `True` moz-phab will create a branch (based on the last revision number)
    for the new DAG branch point.
- `patch.always_full_stack` : When `False` and the patched revision has successors,
    moz-phab will ask if the whole stack should be patched instead. If `True`
    moz-phab will do it without without asking.
- `patch.branch_name_template` : The template string to use for naming the new branch,
    topic or bookmark. The string takes a single format string input, `rev_id`, which
    is the ID of the revision being patched.
- `patch.create_commit` : If `True` (the default) a commit will be generated for
    the patch. Applies the changes with the `patch` command.
- `updater.self_last_check` : Epoch timestamp (local timezone) indicating the last
   time an update check was performed for this script.  set to `-1` to disable
   this check.
- `self_auto_update` : When `True` moz-phab will auto-update if a new version is
    available. If `False` moz-phab will only warn about the new version.
- `get_pre_releases` : When `True` moz-phab auto-update will fetch pre-releases
   if they are available, otherwise pre-releases will be ignored (default: `False`).
- `error_reporting.report_to_sentry` : When `True` moz-phab will submit exceptions
   to Sentry so moz-phab devs can see unreported errors.

### Environment Variables

`moz-phab` can also be configured via the following environment variables:

- `DEBUG` : Enabled debugging output (default: disabled).
- `MOZPHAB_NO_USER_CONFIG` : Do not read from or write to `~/.moz-phab-config`
  (default: disabled).
- `DISABLE_SPINNER` : Set to any value in the environment to disable the spinner
  (default: the spinner is enabled).

## Execution

To get information about all available commands run

```shell
moz-phab -h
```

All commands involving VCS (like `submit` and `patch`) might be used with a
`--safe-mode` switch. It will run the VCS command with only chosen set of extensions.

### Submitting commits to Phabricator

The simplest invocation is

```shell
moz-phab [start_rev] [end_rev]
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

Use `--ai` to request an AI review for all revisions in the stack, including
updates to existing revisions. Or enable the `submit.ai_review` config to
request AI review automatically for new revisions.

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

```shell
moz-phab patch revision_id
```

To patch a stack ending with the revision `D123` run `moz-phab patch D123`.
Diffs will be downloaded from Phabricator and applied using the underlying
VCS (`import` for Mercurial or `apply` for Git). A commit for each revision will
be created in a new bookmark or topic (Mercurial) or branch (Git).

This behavior can be modified with the following options:

- `--apply-to TARGET` Define the commit to which apply the patch:
  - `base` (default) find the base commit in the first ancestor of the revision,
  - `here` use the current commit,
  - `{NODE}` use a commit identified by SHA1 or (in Mercurial) revision number

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

- `--no-topic` : used only when patching a Mercurial repository. Requires the
    `topic` extension to be enabled. If not provided and enabled in the
    configuration - `moz-phab` will create a topic (based on the last revision
    number) for the new DAG branch point. The default behavior [is
    configurable](#configuration).

- `--no-branch`: used only when patching a Git repository. If not provided -
    `moz-phab` will create a branch (based on the revision number). Otherwise
    commits will be added just on top of the *base commit* which might result
    in switching the repository to the 'detached HEAD' state.

- `--skip-dependencies` : patch only one revision, ignore dependencies.

- `--diff-id DIFF_ID`: used to specify a specific diff within a revision's
   history to pull.

### Reorganizing the stack

`moz-phab reorg [start_rev] [end_rev]` allows you to reorganize the stack in Phabricator.

If you've changed the local stack by adding, removing or moving the commits around,
you need to change the parent/child relation of the revisions in Phabricator.

`moz-phab reorg` command will compare the stack, display what will be changed
and ask for permission before taking any action.

This behavior can be modified with the following options:

- `--no-abandon` Avoid abandoning revisions on Phabricator when they have been
  removed from the local stack. Only change the dependency relationships between
  revisions.

### Associating a commit to an existing phabricator revision

`moz-phab` tracks which revision is associated with a commit using a line in the
commit message. If you want to work on an existing revision from a different
machine or environment, we recommend you [apply the existing revision from
Phabricator using `moz-phab patch`](#downloading-a-patch-from-phabricator).

If that isn't an option for whatever reason, you can associate a new commit to
the same revision by adding a line similar to the following to the extended
commit message:

```text
Differential Revision: https://phabricator.services.mozilla.com/D[revision]
```

replacing `[revision]` with the identifier of your revision.

### Submitting an uplift request

`moz-phab uplift` can be used to submit a patch for uplift to a release repository,
bypassing the standard release train cycles. See [the Release Management wiki](https://wiki.mozilla.org/Release_Management/Feature_Uplift)
for more details about uplifts.

To see which trains can be submitted for an uplift request:

```shell
moz-phab uplift --list-trains
```

`moz-phab uplift` uses the same syntax as `moz-phab submit`. To submit an uplift
request against mozilla-beta:

```shell
moz-phab uplift start_rev end_rev --train beta
```

When you submit an uplift within a unified repo (i.e., `mozilla-unified` or `gecko-dev`),
`moz-phab uplift` will attempt to rebase the changes onto the head of the target
train, while keeping the existing revisions in your VCS. When submitting an uplift
from a non-unified repo (i.e., `mozilla-central`, `autoland` etc.) no new changesets
will be created. You can disable the rebasing behaviour with `--no-rebase`.

Once your request has been submitted, navigate to the tip commit of your stack
and request an uplift using the action menu, in the same way you accept a revision.

## Reporting Issues

We use [Bugzilla](https://bugzilla.mozilla.org/) to track development.

File bugs in Bugzilla under
[Conduit :: moz-phab](https://bugzilla.mozilla.org/enter_bug.cgi?product=Conduit&component=moz-phab).

## Development

All python code must be formatted with [black](https://github.com/ambv/black)
using the default settings.

### Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency and Python
version management. Install `uv` by following the
[installation instructions](https://docs.astral.sh/uv/getting-started/installation/).

1. Ensure you have Git and Mercurial installed
   - E.g. using `homebrew` on macOS, or your Linux distribution's package manager
   - `git` and `hg` executables must be on the system path
2. In your clone of this repository, run:

   ```shell
   uv sync --group dev
   ```

   This will install the correct Python version, create a virtual environment,
   and install the project with all dev dependencies.

3. To run moz-phab after making modifications use `uv run moz-phab`
4. To run tests use `uv run pytest -vv`
5. To run tests against a specific Python version use `uv run --python 3.12 pytest -vv`

### Updating dependencies

Dependencies are defined in `pyproject.toml` and locked in `uv.lock`. To update
the lock file after changing dependencies, run:

```shell
uv lock
```

### Circle CI

`mozphab` uses Circle CI to ensure all tests pass on Linux and Windows.

To ensure that your changes work, run `circleci` locally.

1. Ensure you have the `circleci` client installed, see the [CircleCI CLI docs](https://circleci.com/docs/2.0/local-cli/)
2. In your clone of this repository, run:
   `circleci local execute test_3_9`

This will run all the Python 3.9 tests in a dockerized environment.
This step takes a while, so you might want to run `uv run pytest` for working on
your changes, as explained above.

### Submitting patches

Pull Requests are not accepted here; please submit changes to Phabricator using `moz-phab`.

1. Follow the [setup](https://moz-conduit.readthedocs.io/en/latest/phabricator-user.html#setting-up-mozphab)
2. Once your patch is written and committed locally, run `moz-phab` to send it to
   Phabricator.

### Local environment

By using [suite](https://github.com/mozilla-conduit/suite), you can run a local
environment with its own instances of Phabricator, BMO, Hg, and other services.

This enables more thorough integration testing of `moz-phab` without affecting
production data.

You can order the suite to use your local code by calling:

```shell
docker-compose -f docker-compose.yml -f docker-compose.review.yml run local-dev
```

### Creating Releases

To cut a new release of `moz-phab`:

1. Create a tag matching the version number. This will kick off CircleCI jobs
   to generate the release and push it to PyPI.

    ```shell
    git tag -a 1.2.0 origin/main
    git push origin 1.2.0
    ```

2. Post about the new release in the following channels. Run the `dev/release_announcement.py`
   script to generate text for the post.

   - [MozPhab on Mozilla Wiki](https://wiki.mozilla.org/MozPhab/Changelog)
   - [Firefox Tooling Announcements on Discourse](https://discourse.mozilla.org/c/firefox-tooling-announcements)
