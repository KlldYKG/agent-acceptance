# agent-acceptance

> 76% of open source projects with an AI contribution policy ask contributors
> to take responsibility for feature tests. This Action makes that sentence
> execute — and checks whether the submitted evidence is real.

Deterministic test-evidence checks for AI-contributed (or human) PRs:

1. **Bidirectional validity** — a PR's added/modified tests are run against the
   pre-PR revision in a network-isolated sandbox. Fail-before + pass-after =
   **VALID**; already passing on the base = **PRE-PASS** (proves nothing about
   the change — the classic AI-slop signature).
2. **Sampled mutation testing** (mutmut, time-budgeted) — reports which
   behaviors the new tests leave unpinned. Never fabricates numbers: an
   unfinished phase renders as SKIPPED.

Report mode **never blocks merges** (default). Gate mode (required status
check) is opt-in and must be enabled explicitly by maintainers.

No LLM anywhere in the gate. Pairs well with
[Agent Gate](https://github.com/sjh9714/Agent-Gate) for static policy checks.

## Status

🚧 Developer preview (`0.1.0.dev0`). The validated path is the GitHub Action,
dogfooded end-to-end in
[KlldYKG/agent-acceptance-demo](https://github.com/KlldYKG/agent-acceptance-demo)
(PR #1: valid evidence → VALID, mutation score 100%; PR #2: fake evidence →
PRE-PASS, detected).

See `docs/` of the planning workspace for the v1.0 consolidation plan
(CLI worktree backend, packaging, threat model — in progress).
