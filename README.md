## Wrapper around Phabricator's `arc` cli to support submission of a series of commits.

### Installation

Download [moz-phab](https://raw.githubusercontent.com/mozilla-conduit/review/master/moz-phab)
and place it on your system path.

You must have Python 2.7 installed, and preferably in your path.

### Configuration

`moz-phab` has an INI style configuration file to control defaults: `~/.moz-phab-config`

This file will be created if it doesn't exist.

```
[ui]
no_ansi = False

[submit]
auto_submit = False
always_blocking = False
warn_uncommitted = True

[updater]
self_last_check = <time>
arc_last_check = <time>
```

- `ui.no_ansi` : never use ANSI colours (default: auto-detected).
- `submit.auto_submit` : when true the confirmation prompt will be skipped
    (default: false).
- `submit.always_blocking` : when true reviewers in commit descriptions will be marked
    as blocking. reviewers specified on the command line override this setting
    (default: false).
- `submit.warn_uncommiteed` : when true show a warning if there are uncommitted changes
    in the working directory (default: true)
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
non-public, non-obsolete changeset (for Mercurial) and ending with the
currently checked-out changeset.  If only one argument is given, it is
interpreted as the first changeset in the range, with the last again
being the currently checked-out changeset.  If both arguments are
given, they denote the full, inclusive range of changesets.

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

`moz-phab` will periodically check for updates and display a notice
when a new version is available.  To update `moz-phab`, run `moz-phab
self-update`.

Note that if you do not have Python in your path, you will need to run
`<path to python>/python <path to moz-phab>/moz-phab` instead of `moz-phab`.

### Development

- must only use standard libraries
- must be a single file for easy deployment

Unit tests can be executed with `python -m unittest discover`.

All python code must be formatted with [black](https://github.com/ambv/black) using the default settings.
