## Wrapper around Phabricator's `arc` cli to support submission of a series of commits.

### Installation

#### Linux and MacOS

Download moz-phab from the [latest release](https://github.com/mozilla-conduit/review/releases/latest/)
and place it on your system path.

You must have Python 2.7 installed, and preferably in your path.

#### Windows with MozillaBuild/MSYS

Download moz-phab from the [latest release](https://github.com/mozilla-conduit/review/releases/latest/)
and place it on your system path.

You must have Python 2.7 installed, and preferably in your path.

#### Other Windows Installs

Download moz-phab from the [latest release](https://github.com/mozilla-conduit/review/releases/latest/)
and store it anywhere (e.g. `C:\Users\myuser\phabricator\moz-phab`).

You must have Python 2.7 installed, and preferably in your path.

Run python with the full path to moz-phab:
`python C:\Users\myuser\phabricator\moz-phab`.

If you are using `MinTTY` (e.g. via Git's Bash) you'll need to run it through `winpty`
as with any other Python script:
`winpty python C:\Users\myuser\phabricator\moz-phab`.

### Configuration

`moz-phab` has an INI style configuration file to control defaults: `~/.moz-phab-config`

This file will be created if it doesn't exist.

```
[ui]
no_ansi = False

[arc]
arc_command = arc

[submit]
auto_submit = False
always_blocking = False
warn_untracked = True

[updater]
self_last_check = <time>
arc_last_check = <time>
```

- `ui.no_ansi` : never use ANSI colours (default: auto-detected).
- `arc.arc_command` : command to use when calling the Arcanist CLI.
    (default: "arc")
- `submit.auto_submit` : when true the confirmation prompt will be skipped
    (default: false).
- `submit.always_blocking` : when true reviewers in commit descriptions will be marked
    as blocking. reviewers specified on the command line override this setting
    (default: false).
- `submit.warn_untracked` : when true show a warning if there are uncommitted or
    untracked changes in the working directory (default: true)
- `updater.self_last_check` : epoch timestamp (local timezone) indicating the last time
    an update check was performed for this script.  set to `-1` to disable this check.
- `updater.arc_last_check` : epoch timestamp (local timezone) indicating the last time
    an update was performed for arc.  set to `-1` to disable this check.

`moz-phab` can also be configured via the following environmental variables:
- `DEBUG` : enabled debugging output (default: disabled)
- `UPDATE_FILE` : when self-updating write to this file instead of \_\_file\_\_

### Execution

The simplest invocation is

```
  $ moz-phab submit [start_rev] [end_rev]
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

`moz-phab` will periodically check for updates and display a notice
when a new version is available.  To update `moz-phab`, run `moz-phab
self-update`.

Note that if you do not have Python in your path, you will need to run
`<path to python>/python <path to moz-phab>/moz-phab` instead of `moz-phab`.

### Development

File bugs in Bugzilla under
[Conduit :: Review Wrapper](https://bugzilla.mozilla.org/enter_bug.cgi?product=Conduit&component=Review%20Wrapper).

We have strict requirements for moz-phab development:

- must only use standard libraries
- must be a single file for easy deployment

Unit tests can be executed with `python -m unittest discover`.

All python code must be formatted with [black](https://github.com/ambv/black) using the default settings.
