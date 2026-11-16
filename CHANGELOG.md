# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/); versioning
follows [SemVer](https://semver.org/). Policy and protocol semantics follow
`protocol/SPEC.md` v2 (frozen) in the project workspace.

## [Unreleased]

### Fixed

- **Loud failure for a dead pytest interpreter (G11).** A relative `--python`
  path used to evaporate after the child process switched its working
  directory to the worktree: pytest silently never ran and the whole run
  degraded into an `ERROR` verdict with zero diagnostics. The interpreter is
  now resolved to an absolute path up front (both the overlay backend and the
  mutation backend), and a run that produces no JUnit XML at all raises a
  `PytestRunnerError` carrying the captured output tail — pytest that *starts*
  always writes JUnit XML, so missing XML is an infrastructure failure and
  must be reported as one.

## [1.0.0] — 2026-11-16

First stable release. The validated execution path (GitHub Action, Docker
sandbox) and the local CLI now come from the same source of truth.

### Added

- **Bidirectional test-evidence verification.** Added/modified tests from a PR
  are extracted, overlaid onto the pre-PR revision, and executed twice in a
  network-isolated sandbox. `fail-before → pass-after` = **VALID**;
  already-passes-on-base = **PRE-PASS** (the AI-slop signature); fails on both =
  **BROKEN**; collection failure = **ERROR**. Verdicts are deterministic — no
  LLM in the loop.
- **Sampled mutation testing** (mutmut 2.5.x) over the PR's changed source
  files only, with a wall-clock budget (default 600 s). Budget overrun renders
  as `TIMEOUT`, engine unavailable as `SKIPPED` — the report never fabricates
  numbers.
- **GitHub Action** (`action/`): composite action with Docker sandbox
  (no network, read-only FS, dropped capabilities, non-root), PR-comment report
  with upsert semantics, and opt-in **gate mode**. Deterministic report
  rendering (`report/render.py`).
- **CLI** (`agent-acceptance` on PyPI): `run` (git-worktree overlay backend,
  local), `evaluate`, `report` subcommands; documented exit codes
  (0 OK / 10 INVALID_EVIDENCE / 11 BROKEN / 20 NO_EVIDENCE / 2 usage).
- **Base-branch-only policy** (`.agent-acceptance.yml`): test dirs, mutation
  budget, flaky-n, gate flag — read from the base revision only, so a PR can
  never alter the checks applied to itself.
- **Gate-mode flaky rule** (SPEC §2.5 extension): each phase runs n=3 times;
  unstable tests are reported as errors and never counted as evidence.
  Report mode always runs each side once.
- **Security documentation**: `docs/THREAT_MODEL.md` (STRIDE-lite, T1–T8 →
  mitigations, residual risks) and `docs/GATE_MODE.md` (branch-protection
  integration, fail-closed semantics).
- **CI**: ruff + mypy + pytest with ≥80% coverage gate on Python 3.10–3.13.
  50 tests, 93.78% coverage at release.

### Changed

- Report/eval I/O pins UTF-8 explicitly (reports contain emoji; GBK-default
  Windows hosts crashed before).
- The demo repository consumes this action by full-commit-SHA reference
  instead of a vendored copy (single source of truth).

### Security

- Action code always executes from the pinned ref of this repository, never
  from the PR (safe `pull_request_target` pattern, double checkout).
- The default is **report mode** (comment only, never blocks). Gate mode is
  opt-in and fails closed.
- Residual risks (notably: `install-deps` executes packaging code of
  dependencies, networked) are documented in `docs/THREAT_MODEL.md`.

### Known limitations

- pytest-ecosystem projects only (JUnit XML interface).
- `SKIPPED`/`TIMEOUT` mutation results are neutral, not negative.
- CLI `run` backend executes tests locally without sandboxing — use the
  Action for untrusted code.

[1.0.0]: https://github.com/KlldYKG/agent-acceptance/releases/tag/v1.0.0
