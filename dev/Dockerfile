# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# Development image for running moz-phab benchmarks under valgrind.
#
# Codspeed's deterministic measurement mode
# (`--codspeed-mode=instrumentation`) requires valgrind, which is
# awkward to install on macOS and pulls in a chunk of toolchain on
# Linux. This image ships the toolchain so contributors can get
# reproducible instruction-count numbers without polluting the host.
#
# Build:
#   docker build -t moz-phab-bench -f dev/Dockerfile .
#
# Run benchmarks (mount the repo so results land back in .codspeed/):
#   docker run --rm -v "$PWD":/work -w /work moz-phab-bench \
#       uv run pytest --codspeed --codspeed-mode=instrumentation tests/benchmarks/

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        valgrind \
    && rm -rf /var/lib/apt/lists/*

# Pull `uv` from the official prebuilt image rather than running an
# install script: faster, version-pinnable, no curl-to-shell.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Install codspeed-runner. pytest-codspeed's instrumentation mode is
# inert without it -- the runner is what drives valgrind/cachegrind and
# produces a profile pytest-codspeed can decode. Pin to a known version
# so the image is reproducible.
ARG CODSPEED_RUNNER_VERSION=v4.16.1
RUN set -eux \
    && curl -fsSL "https://github.com/CodSpeedHQ/runner/releases/download/${CODSPEED_RUNNER_VERSION}/codspeed-runner-x86_64-unknown-linux-musl.tar.gz" \
        -o /tmp/codspeed.tar.gz \
    && tar -xzf /tmp/codspeed.tar.gz -C /tmp \
    && mv /tmp/codspeed-runner-x86_64-unknown-linux-musl/codspeed /usr/local/bin/codspeed \
    && chmod +x /usr/local/bin/codspeed \
    && rm -rf /tmp/codspeed.tar.gz /tmp/codspeed-runner-x86_64-unknown-linux-musl

# Minimal git config so commit-related operations in tests don't trip
# on a missing identity, and `safe.directory` so bind-mounted repos
# whose owner uid differs from the container user (the common macOS
# and Linux-rootless case) don't fail with "dubious ownership".
RUN git config --global user.email "moz-phab-bench@example.com" \
    && git config --global user.name "moz-phab bench" \
    && git config --global --add safe.directory '*'

# Pin uv's interpreter and project-environment paths so a bind-mount of
# the repo doesn't trigger re-resolution against a different Python or
# clobber the host's `.venv` directory.
ENV UV_PYTHON=3.12
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Pre-install dependencies so the first benchmark run inside the
# container doesn't pay a full sync. Only the manifest files are
# copied in -- the project itself is installed by `uv run` once the
# repo is bind-mounted at runtime.
WORKDIR /work
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --group dev --no-install-project --frozen
