"""agent-acceptance: deterministic test-evidence checks for contributed PRs.

Report mode (default) posts a PR comment assessing whether added/modified
tests are real evidence: fail-before + pass-after = VALID; already-passing
on the base revision = PRE-PASS (proves nothing about the change). Optional
time-budgeted mutation sampling reports which behaviors the new tests leave
unpinned. No LLM anywhere in the gate.
"""

__version__ = "1.0.0"
