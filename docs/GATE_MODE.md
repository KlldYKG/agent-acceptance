# Gate Mode (opt-in blocking)

> **Default is report mode: the Action posts a comment and never blocks a
> merge.** Gate mode is a deliberate maintainer decision, configured in one
> place — the base branch of your repository. A PR cannot enable it for
> itself.

## What gate mode does

With `gate: true`, the Action exits non-zero when:

| condition | verdict | exit code |
|---|---|---|
| no test shows valid evidence (all PRE-PASS / no usable rows / collection failure) | `INVALID_EVIDENCE` or `ERROR` | `10` |
| any changed test fails on *both* revisions | `BROKEN` (test itself is broken) | `11` |

`PRE-PASS` alone never blocks a benign refactor; `PARTIAL` (some valid +
some broken/pre-pass) reports but does not block. Gate semantics are defined
in SPEC v2 §5.

## 1. Enable in the workflow

```yaml
- uses: ./aa-trusted/tool/action
  with:
    github-token: ${{ github.token }}
    gate: true            # opt-in: fail the job on invalid evidence
    flaky-n: 3            # (optional) gate-mode flaky rule: 3 consistent runs required
```

CLI equivalent:

```bash
agent-acceptance run --repo . --base main --head feature --out out \
  --gate                 # exit 10/11 instead of just reporting
```

## 2. Make it a required status check

1. Repository → **Settings → Branches → Add branch protection rule** for
   your default branch.
2. Enable **Require status checks to pass before merging**.
3. Add the check named exactly after the job:

   ```text
   evidence
   ```

   (from the quick-start workflow: job id `evidence` in workflow
   `agent-acceptance`). If you renamed the job, use that name — the required
   check matches the **job id**, not the workflow name.
4. Recommended companions: *Require branches to be up to date* off (the
   action runs per-PR against its merge ref), and admin enforcement on.

Result: the PR shows `agent-acceptance / evidence` as a required check;
`INVALID_EVIDENCE` or `BROKEN` blocks the merge button even though the
comment itself is advisory.

## 3. Policy lives on the base branch only

Prefer the repository policy file over workflow inputs so the policy is
versioned and PR-immune:

```yaml
# .agent-acceptance.yml  (on the DEFAULT branch)
gate: true
flaky-n: 3
mutation-budget-seconds: 600
test-dirs: tests
```

`.agent-acceptance.yml` is read exclusively from the base revision
(`git show <base>: .agent-acceptance.yml`). CLI flags override it for local
runs, but on CI the base policy is authoritative.

## 4. Fail-closed vs fail-open

Gate mode **fails closed** on tool errors (`ERROR` verdict → exit `10`): if
the harness cannot establish evidence quality, the check fails and a
maintainer must look. If you prefer fail-open (e.g. while evaluating the
tool), stay in report mode or set `--report-only` / `gate: false`.

## Known interaction

GitHub is tightening default `pull_request_target` handling (announced for
2026-12). After that change, repositories may need to allow the workflow via
**Settings → Actions → General** ("Require approval for external
contributors" or an allowlist). The quick-start double-checkout pattern in
the README remains the safe shape; review that setting when enabling gate
mode on a repository that accepts first-time-contributor PRs.
