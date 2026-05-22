# Benchmarking moz-phab

`moz-phab` uses [codspeed.io](https://codspeed.io) for performance
benchmarking of the `submit` and `patch` workflows.

## What's covered today

End-to-end benchmarks under `tests/benchmarks/` drive the real CLI
through `mozphab.main([...], is_development=True)` against a real git
repository (`git_repo_path` from `tests/conftest.py`). Only the HTTP
boundary (`ConduitAPI.call`) is mocked, via a function-based
module-level `call_conduit`:

- `tests/benchmarks/test_bench_submit.py` — `moz-phab submit` for a
  single new commit. Exercises the real `Repository` layer (real git
  subprocesses), the real `SimpleCache`, and the parallel
  `request_ai_reviews` `ThreadPoolExecutor` fan-out.
- `tests/benchmarks/test_bench_patch.py` — `moz-phab patch D1 --raw`
  for a single revision. Exercises the 3-way `ThreadPoolExecutor`
  (ping + check_vcs + is_worktree_clean), the parallel
  `differential.getrawdiff` fan-out, and the real `Repository` /
  `SimpleCache`.

## Running benchmarks locally

```shell
uv run pytest --codspeed tests/benchmarks/
```

Walltime mode is the default; it prints a table of best/mean times,
relative stdev, and iteration count per benchmark. It's the same mode
the CI job uses, so local numbers correspond to what the dashboard
records (modulo machine variance). Expect 10-25% relative stdev on a
typical dev box; small wins (~5%) will be hard to spot in noise.

## CI integration

The `benchmarks` job in `.github/workflows/ci.yml` runs on every push
to any branch in the upstream repository. It authenticates to
codspeed.io via [OpenID Connect](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect):
no `CODSPEED_TOKEN` secret is required. Results upload to the moz-phab
project on the codspeed.io dashboard in **walltime mode**.

There is no `pull_request` trigger and no PR-comment workflow --
`moz-phab` reviews land on Phabricator, so the codspeed dashboard
serves as the perf history surface rather than a per-PR comment. Once
a commit lands on `main` and the `benchmarks` job runs against it,
every subsequent push to any branch produces a per-benchmark delta
against that baseline on the dashboard.

### Why walltime over CPU simulation

codspeed's other measurement mode, "CPU simulation"
(`--codspeed-mode=instrumentation` on the pytest plugin), runs the
benchmark under `valgrind` + `cachegrind` and reports instruction
counts. That mode is deterministic and noise-free, which is appealing
in principle.

For `moz-phab` it doesn't work. The dashboard explicitly warns about
this when simulation mode is used: a single `moz-phab submit` makes
~680 system calls and spends the bulk of its time inside git
subprocesses and (in production) HTTPS round-trips. Simulation
instruments only the parent Python process, so it measures < 1% of
the actual work and reports an effectively empty flamegraph.

Walltime captures the real wall-clock time of the workflow, including
subprocess work and (when present) network I/O. The cost is higher
per-run variance -- codspeed recommends their dedicated "Macro
Runners" for tight walltime measurements; on standard GHA runners,
deltas under ~10% are usually in the noise floor.

## Adding a new benchmark

Drop a file at `tests/benchmarks/test_bench_<name>.py` with one or
more `@pytest.mark.benchmark`-decorated functions. The existing
benchmarks are the canonical pattern: module-level `call_conduit =
mock.Mock()` (picked up by the `in_process` fixture), function-based
`call_conduit.side_effect` so codspeed iterations don't exhaust a
fixed response sequence, and `git_repo_path` for the real-git
exercise. See `tests/benchmarks/test_bench_submit.py` for the full
shape.

## Limitations of codspeed for `moz-phab`

Documented so future contributors don't expect signals that aren't
there:

- **TLS connection pooling and other HTTPS-level optimisations** can't
  be measured by codspeed. The TLS handshake is OpenSSL (C code), and
  the benchmarks mock the HTTP boundary anyway. The validation for
  that kind of optimisation belongs at the time of the change, not in
  continuous measurement.
- **Subprocess CPU time** (git, hg, jj) runs outside the measured
  Python process. Walltime mode picks it up because we time the
  whole workflow wall-clock; CPU simulation mode would miss it
  entirely (which is why we use walltime -- see above).
- **Network RTT to Phabricator** is fully mocked in benchmarks.
  Real-world `submit` latency depends heavily on Conduit response
  time, which we don't attempt to model.

## Acknowledgements

This setup was inspired by Sylvestre Ledru's benchmark-framework
prototype in [D295969](https://phabricator.services.mozilla.com/D295969),
which drove the real `mozphab.conduit` helpers through a mocked
`ConduitAPI.call` with simulated per-call latency. The submit and
patch workflow coverage tracked here matches that prototype's intent,
substituting codspeed for the bespoke framework and exercising the
real `Repository` layer against a real git repo rather than a
`MagicMock`.
