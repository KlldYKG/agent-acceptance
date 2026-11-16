"""Exit code semantics (P1_CONSOLIDATION_PLAN_CN.md, output 2)."""

OK = 0
"""Report published (or gate mode passed); nothing abnormal."""

GATE_INVALID_EVIDENCE = 10
"""Gate mode only: evidence is INVALID (all tests PRE-PASS / no valid evidence)."""

GATE_BROKEN = 11
"""Gate mode only: a changed test is BROKEN (fails on both revisions)."""

BROKEN_GOLD = 20
"""The harness itself is misconfigured (e.g. a gold test passes on base).
Isolated as a tooling/dataset error — never counted as agent fault."""

USAGE = 2
"""Bad invocation, missing files, or infrastructure error."""
