# agent-acceptance

> 76% of open source projects with an AI contribution policy ask contributors
> to "take responsibility for tests". This Action makes that sentence execute —
> and checks whether the submitted evidence is real.

Deterministic, execution-layer test-evidence checks for AI-contributed (or
human) pull requests. No LLM anywhere in the loop.

## What it checks

**1. Bidirectional test validity.** The PR's added/changed tests are run
against *both* revisions in a network-isolated sandbox:

| on base | on PR | verdict | meaning |
|---|---|---|---|
| ❌ fails | ✅ passes | **VALID** | real evidence: the test pins the change |
| ✅ passes | ✅ passes | **PRE-PASS** | proves nothing about the change — the classic AI-slop signature |
| ❌ fails | ❌ fails | **BROKEN** | the test itself is broken |
| — | error | **ERROR** | collection/execution failure |

**2. Sampled mutation testing** (mutmut 2.5.x, time-budgeted, scoped to the
PR's changed source files): quantifies how tightly the new tests pin down the
changed behavior. **Never fabricates numbers** — an unfinished phase renders
as `SKIPPED`, not a made-up score.

Verified end-to-end in
[agent-acceptance-demo](https://github.com/KlldYKG/agent-acceptance-demo):
PR #1 (real fix + real tests) → `VALID`, mutation score 100%; PR #2 (tests
that pass without any fix, i.e. slop) → `PRE-PASS`, detected.

## Safety model (summary — full version below)

- **Untrusted code never gets network access.** Test and mutation phases run
  in a container with `--network none`, read-only repo mount, dropped
  capabilities, non-root UID, CPU/memory/PID limits.
- **The only networked phase is opt-in dependency installation** (`install-deps`:
  separate container, binary wheels only, hard timeout).
  ⚠️ **Residual risk**: installing a package executes its packaging code
  (`setup.py`/`build` hooks). Only enable `install-deps` when you accept
  running the PR's declared dependencies' build code on the runner.
- **The Action's own code always executes from your default branch**, never
  from the PR (safe `pull_request_target` double-checkout pattern).
- **Report mode (default) never blocks merges.** Gate mode is opt-in and must
  be enabled by a maintainer — see [docs/GATE_MODE.md](docs/GATE_MODE.md).

Full analysis: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## Quick start (GitHub Action)

In the target repository, create `.github/workflows/agent-acceptance.yml`:

```yaml
name: agent-acceptance
on: pull_request_target
permissions:
  contents: read
  pull-requests: write
jobs:
  evidence:
    runs-on: ubuntu-latest
    steps:
      # Code under test: the PR merge snapshot (full history for git archive).
      - uses: actions/checkout@v4
        with:
          ref: refs/pull/${{ github.event.pull_request.number }}/merge
          fetch-depth: 0
      # Action code: ALWAYS from the trusted default branch, never from the PR.
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.repository.default_branch }}
          path: aa-trusted
      - uses: ./aa-trusted/tool/action   # pin a release tag / SHA in production
        with:
          github-token: ${{ github.token }}
```

The action posts **one** PR comment (upserted in place) with the per-test
verdict table, mutation results, and a reading guide.

### Inputs

| input | default | notes |
|---|---|---|
| `github-token` | `${{ github.token }}` | used only to upsert the PR comment |
| `test-dirs` | `tests` | comma-separated |
| `mutation-budget-seconds` | `600` | overrun ⇒ `SKIPPED` (no score) |
| `install-deps` | `false` | ⚠️ see safety model |
| `gate` | `false` | opt-in blocking — [docs/GATE_MODE.md](docs/GATE_MODE.md) |

## Quick start (CLI)

The same checks, runnable locally against any git repository:

```bash
pip install agent-acceptance

# Verify a branch against main (writes out/base.xml, out/pr.xml,
# out/evidence.json):
agent-acceptance run --repo . --base main --head my-branch --out out

# Or evaluate pre-captured JUnit XML:
agent-acceptance evaluate out --base-sha <sha> --head-sha <sha>

# Render the deterministic PR-comment Markdown:
agent-acceptance report out
```

Exit codes: `0` ok · `10` gate: invalid evidence · `11` gate: broken test ·
`20` harness misconfiguration · `2` usage/infrastructure error.

Policy (`.agent-acceptance.yml`) is loaded **only from the base revision** —
a PR can never alter the checks applied to itself.

## How this differs from review bots and static gates

| | LLM review bots | static policy gates (e.g. [Agent Gate](https://github.com/sjh9714/Agent-Gate)) | **agent-acceptance** |
|---|---|---|---|
| What it inspects | diff + prose | files/patterns | **executed behavior** |
| Can be fooled by plausible-sounding tests | yes | n/a | no — tests must actually fail-then-pass |
| Deterministic / replayable | no | yes | yes |
| Sees whether tests pin behavior | no | no | yes (mutation sampling) |

agent-acceptance is complementary to static gates: it verifies *execution
evidence*, they verify *policy compliance*.

## Status

**v1.0.0** — first stable release. Install from
[PyPI](https://pypi.org/project/agent-acceptance/) or use the action
(pinned by commit SHA, see the quick start above). See
[CHANGELOG.md](CHANGELOG.md) for the release notes.

## Contributing & license

Apache-2.0. Issues and PRs welcome — evidence-based tests especially.
