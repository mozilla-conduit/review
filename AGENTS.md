## Project Overview

MozPhab (`moz-phab`) is Mozilla's CLI tool for submitting and managing commit series on Phabricator. It supports Git, Mercurial, and Jujutsu (experimental) version control systems. See `pyproject.toml` (`project.requires-python`) for the supported Python versions.

## Common Commands

```bash
# Install dependencies (uses uv package manager)
uv sync --group dev

# Run the tool locally
uv run moz-phab

# Run all tests
uv run pytest -vv

# Lint
uv run ruff check .

# Format
uv run black .
```

## Code Style

See `ruff.toml` for linting configuration; `black` is configured with the default line length of 88.

## Architecture

**Entry point**: `mozphab/mozphab.py::run()` → parses args, loads config, detects repository type, dispatches to command.

**VCS layer**: Abstract `Repository` base class (`repository.py`) with implementations in `git.py`, `mercurial.py`, `jujutsu.py`. Each implements commit operations, diff extraction, worktree management.

**Commands** (`mozphab/commands/`): Each command is a module — `submit.py` (main feature: push commit stacks to Phabricator), `patch.py` (apply patches), `reorganise.py`, `uplift.py`, `abandon.py`, `doctor.py`, `install_certificate.py`, `self_update.py`, `version.py`.

**Phabricator API**: `conduit.py` — wraps Conduit API calls, handles authentication, search, revision management.

**Config**: `config.py` manages INI-style user config at `~/.moz-phab-config`.

**Telemetry**: Glean SDK integration (`telemetry.py`, `metrics.yaml`, `pings.yaml`).

## Testing

- Unit tests and integration tests live in `tests/`.
- Integration tests (`test_integration_*.py`) create real Git/Hg repositories.
- `conftest.py` provides fixtures: `git_repo_path`, `hg_repo_path`, mocked Conduit responses.
- The `fresh_global_config` fixture is applied globally via `pytest.ini`.
- Marker `no_mock_token` skips automatic token mocking for tests that need it.
- Tests set `MOZPHAB_NO_USER_CONFIG=1` to avoid loading user config.
- `tests/test_style.py` runs `ruff` and `black` as part of the normal test suite.

## MCP resources

- `@moz:bugzilla://bug/{bug_id}` — retrieve a bug.
- `@moz:phabricator://revision/D{revision_id}` — retrieve a Phabricator revision and its comments.
