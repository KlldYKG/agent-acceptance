"""Classify bidirectional test validity + mutation results.

Migrated from the P1a prototype (tool/action/scripts/evaluate.py) into a
testable package. Pure stdlib; deterministic; no LLM.

Class semantics (SPEC v2, P1_CONSOLIDATION_PLAN output 2):
- VALID:     test fails on base, passes on PR  -> real evidence.
- PRE-PASS:  test already passes on base       -> proves nothing about the change.
- BROKEN:    test fails on both revisions      -> the test itself is broken.
- ERROR:     run collection failed (missing result, test errored).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

VERDICTS = ("VALID", "PRE-PASS", "BROKEN", "ERROR")

STRENGTH_ORDER = {"NONE": 0, "WEAK": 1, "PARTIAL": 2, "STRONG": 3}


@dataclass
class TestRow:
    key: str
    on_base: str
    on_pr: str
    verdict: str
    note: str = ""


@dataclass
class MutationResult:
    status: str = "SKIPPED"  # OK | SKIPPED | TIMEOUT
    killed: int = 0
    survived: int = 0
    score: float | None = None
    survivor_samples: list[dict] = field(default_factory=list)


@dataclass
class Evidence:
    base_sha: str
    head_sha: str
    strength: str  # NONE | WEAK | PARTIAL | STRONG
    verdict: str  # EVIDENCE_OK | INVALID_EVIDENCE | ERROR | NO_EVIDENCE
    tests: list[TestRow]
    mutation: MutationResult

    @property
    def counts(self) -> dict[str, int]:
        return {v: sum(1 for t in self.tests if t.verdict == v) for v in VERDICTS}

    def to_json(self) -> str:
        doc = {
            "schema": "agent-acceptance/evidence@v0",
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "strength": self.strength,
            "verdict": self.verdict,
            "tests": [
                {"test": t.key, "on_base": t.on_base, "on_pr": t.on_pr,
                 "verdict": t.verdict, "note": t.note}
                for t in self.tests
            ],
            "counts": self.counts,
            "mutation": {
                "status": self.mutation.status,
                "score": self.mutation.score,
                "killed": self.mutation.killed,
                "survived": self.mutation.survived,
                "survivor_samples": self.mutation.survivor_samples,
            },
        }
        return json.dumps(doc, indent=2)


def parse_junit(path: Path) -> dict[str, str]:
    """Return {test_key: outcome}; outcome in {passed, failed, error}.

    Missing file -> empty dict (caller treats as collection failure).
    """
    if not path.exists():
        return {}
    outcomes: dict[str, str] = {}
    root = ET.parse(path).getroot()
    for case in root.iter("testcase"):
        key = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None or case.find("assertion") is not None:
            outcomes[key] = "failed"
        elif case.find("error") is not None:
            outcomes[key] = "error"
        else:
            outcomes[key] = "passed"
    return outcomes


def parse_mutation(path: Path, forced_status: str | None = None) -> MutationResult:
    """Parse a mutmut junitxml (one testcase per mutant).

    mutmut marks *survived* mutants with a <skipped> element. If the phase did
    not finish within budget (forced_status set), we report the declared status
    and never fabricate a score. An XML with **zero** testcase elements means
    mutmut never measured a mutant (typically: the baseline test run failed —
    e.g. broken PR tests abort the mutation baseline), so the phase reports
    SKIPPED rather than an OK/null score.
    """
    if forced_status is not None or not path.exists():
        return MutationResult(status=forced_status or "SKIPPED")
    killed = survived = 0
    samples: list[dict] = []
    for case in ET.parse(path).getroot().iter("testcase"):
        desc = f"{case.get('classname', '')}::{case.get('name', '')}"
        skipped = case.find("skipped")
        if skipped is not None:
            survived += 1
            if len(samples) < 5:
                samples.append(
                    {"mutant": desc, "detail": (skipped.get("message", "") or "")[:160]}
                )
        else:
            killed += 1
    total = killed + survived
    if total == 0:
        # No mutant was ever measured: an OK/null score here would read as
        # "phase ran, nothing to report" when in fact it never ran.
        return MutationResult(status="SKIPPED")
    return MutationResult(
        status="OK",
        killed=killed,
        survived=survived,
        score=round(killed / total, 3) if total else None,
        survivor_samples=samples,
    )


def classify_pair(on_base: str, on_pr: str) -> str:
    """Per-test verdict from the (base outcome, PR outcome) pair."""
    if on_base == "failed" and on_pr == "passed":
        return "VALID"
    if on_base == "passed" and on_pr == "passed":
        return "PRE-PASS"
    if on_pr in ("missing", "error") or on_base == "error":
        return "ERROR"
    return "BROKEN"


def classify_run(
    base: dict[str, str], pr: dict[str, str], mutation: MutationResult
) -> tuple[str, str, list[TestRow]]:
    """Aggregate per-test rows into (strength, verdict).

    strength: NONE (no usable rows) | WEAK (only PRE-PASS) |
              PARTIAL (some VALID but incomplete) | STRONG (all VALID).
    verdict:  EVIDENCE_OK | INVALID_EVIDENCE | ERROR.
    """
    rows = [
        TestRow(key=key, on_base=outcome, on_pr=pr.get(key, "missing"),
                verdict=classify_pair(outcome, pr.get(key, "missing")))
        for key, outcome in base.items()
    ]
    if not rows:
        return "NONE", "ERROR", rows

    n = len(rows)
    valid = sum(r.verdict == "VALID" for r in rows)
    errored = sum(r.verdict == "ERROR" for r in rows)
    broken = sum(r.verdict == "BROKEN" for r in rows)

    if errored == n:
        strength, verdict = "NONE", "ERROR"
    elif valid == n:
        strength, verdict = "STRONG", "EVIDENCE_OK"
    elif valid > 0:
        # Some real evidence; PARTIAL never blocks in gate mode — only a run
        # with *no* valid evidence at all is gate-worthy invalid.
        strength, verdict = "PARTIAL", "EVIDENCE_OK"
    elif broken > 0:
        strength, verdict = "NONE", "INVALID_EVIDENCE"
    else:  # only PRE-PASS rows
        strength, verdict = "WEAK", "INVALID_EVIDENCE"
    return strength, verdict, rows


def evaluate(
    out_dir: Path, base_sha: str, head_sha: str, mut_status: str | None = None
) -> Evidence:
    """Full evaluation over a directory containing base.xml / pr.xml / mutation.xml."""
    out_dir = Path(out_dir)
    base = parse_junit(out_dir / "base.xml")
    pr = parse_junit(out_dir / "pr.xml")

    forced_mut = mut_status if mut_status and mut_status != "OK" else None
    mut = parse_mutation(out_dir / "mutation.xml", forced_status=forced_mut)

    if not base or not pr:
        strength, verdict = "NONE", "ERROR"
        rows: list = []
    else:
        strength, verdict, rows = classify_run(base, pr, mut)

    return Evidence(
        base_sha=base_sha,
        head_sha=head_sha,
        strength=strength,
        verdict=verdict,
        tests=rows,
        mutation=mut,
    )
