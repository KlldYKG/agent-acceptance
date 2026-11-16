# Threat Model (STRIDE-lite)

Scope: the agent-acceptance GitHub Action and CLI, which execute code from
untrusted pull requests on maintainer infrastructure. Adversaries: PR
authors (hostile or careless), and anyone who can open a PR to a repository
running this Action.

## Assets

1. The runner (and, for self-hosted runners, the host network).
2. Repository secrets available to the workflow (`GITHUB_TOKEN` scope, plus
   any secrets the maintainer added).
3. The integrity of the PR comment (maintainer-facing signal).
4. The target repository's default branch.

## Threats → mitigations

| # | Threat (STRIDE) | Scenario | Mitigation |
|---|---|---|---|
| T1 | Tampering / Elevation (info disclosure via network) | PR code phones home, exfiltrates runner env/secrets, or attacks other hosts | Test & mutation phases run with `--network none`; repo mounted `--read-only`; `--cap-drop ALL`, `no-new-privileges`, non-root UID, `--pids-limit`, CPU/memory caps; scratch is tmpfs only |
| T2 | Tampering (workflow injection) | PR modifies workflow or Action code that would then run with `pull_request_target` privileges | Double checkout: the Action's code always comes from the default branch (`aa-trusted` path); PR workflow files are never treated as instructions; documented safe `pull_request_target` pattern |
| T3 | Elevation via dependency install | `requirements.txt` in the PR pins a malicious package whose build code runs networked | `install-deps` is **off by default**; when enabled: separate container, `--only-binary=:all:`, hard 900 s timeout, results mounted read-only. **Residual risk (see below)** |
| T4 | Repudiation / integrity of the verdict | Comment tampering or "score inflation" | Report is a deterministic projection of `evidence.json`; comment upserted via hidden marker (one comment per PR); mutation phase **never fabricates scores** (unfinished ⇒ `SKIPPED`) |
| T5 | Policy tampering by the PR | PR weakens its own checks via `.agent-acceptance.yml` | Policy is read from the **base revision only** (`git show base:…`); PR content cannot influence configuration |
| T6 | DoS the maintainer's CI minutes | Giant generated tests or mutants burn the budget | Time budgets: mutation 600 s (default) hard-capped; test phase bounded by CI job limits; mutation overrun degrades to `SKIPPED` |
| T7 | Flaky tests gaming the verdict | Tests pass only sometimes | Gate mode supports `flaky-n` repeated runs (default 3); unstable outcomes count as `ERROR`, never evidence |
| T8 | Comment spoofing via PR content | Report text contains markdown injected from test names/messages | Renderer escapes/limits injected fields; survivor samples truncated to 160 chars; fixed template, no free-form PR text |

## Residual risks (accepted, disclosed)

- **`install-deps: true` executes packaging code.** Installing a wheel/sdist
  can run `setup.py` and build hooks from the PR's dependency set, inside a
  *networked* container. This is the single highest-risk opt-in. Mitigate by
  reviewing `requirements.txt` changes before enabling, and preferring
  wheels from well-known packages.
- **Docker-on-runner trust boundary.** The sandbox assumes the runner's
  Docker daemon is trusted. On GitHub-hosted runners this is GitHub's
  boundary; on **self-hosted** runners the maintainer owns it — container
  escapes are out of scope for this tool.
- **`pull_request_target` semantics.** The workflow runs with read/write on
  `GITHUB_TOKEN` scoped by the declared `permissions:` block. Keep
  `permissions` minimal (`contents: read`, `pull-requests: write`) as in the
  quick-start template. GitHub's 2026-12 policy tightening on
  `pull_request_target` may require opting in per-repository — pinned in
  [docs/GATE_MODE.md](docs/GATE_MODE.md).
- **CLI (`run`) has no sandbox.** The local worktree backend executes the
  target project's tests with your own Python interpreter, unsandboxed. It
  is a developer convenience; use it on code you already trust.

## Known limitations

- Single pytest ecosystem (Python). Other languages are out of scope for v1.
- Mutation sampling is budget-bounded: `SKIPPED` means "unknown", not "bad".
- Per-test repetition (flaky rule) is gate-mode-only; report mode runs each
  test once per side.
- JUnit XML keys are `classname::name`; renamed test classes are treated as
  different tests.
