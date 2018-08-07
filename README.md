## Wrapper around Phabricator's `arc` cli to support submission of a series of commits.


### Goals

- must only use standard libraries
- must be a single file for easy deployment
- should work on python 2.7 and python 3.5+

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

`review` can also be configured via the following environmental variables:
- `DEBUG` : enabled debugging output (default: disabled)
- `UPDATE_FILE` : when self-updating write to this file instead of \_\_file\_\_

e.g. To enable debugging output on MacOS/Linux:
```
  $ DEBUG=1 review submit
```

Unit tests can be executed with `python -m unittest discover`.

