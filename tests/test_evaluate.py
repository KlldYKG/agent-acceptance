"""Tests for the bidirectional-validity classifier and mutation parsing."""

from pathlib import Path

import pytest

from agent_acceptance.core.evaluate import (
    MutationResult,
    classify_pair,
    classify_run,
    evaluate,
    parse_junit,
    parse_mutation,
)


# ---------------------------------------------------------------- classify_pair
@pytest.mark.parametrize(
    ("base", "pr", "expected"),
    [
        ("failed", "passed", "VALID"),
        ("passed", "passed", "PRE-PASS"),
        ("failed", "failed", "BROKEN"),
        ("passed", "failed", "BROKEN"),
        ("passed", "missing", "ERROR"),
        ("passed", "error", "ERROR"),
        ("error", "passed", "ERROR"),
        ("error", "missing", "ERROR"),
    ],
)
def test_classify_pair_matrix(base, pr, expected):
    assert classify_pair(base, pr) == expected


# ---------------------------------------------------------------- classify_run
def _rows(base, pr):
    b = {f"t{i}": v for i, v in enumerate(base)}
    p = {f"t{i}": v for i, v in enumerate(pr)}
    return classify_run(b, p, MutationResult())


def test_strong_when_all_valid():
    strength, verdict, rows = _rows(["failed", "failed"], ["passed", "passed"])
    assert (strength, verdict) == ("STRONG", "EVIDENCE_OK")
    assert all(r.verdict == "VALID" for r in rows)


def test_weak_when_only_prepass():
    strength, verdict, _ = _rows(["passed"], ["passed"])
    assert (strength, verdict) == ("WEAK", "INVALID_EVIDENCE")


def test_partial_when_mixed_valid_and_prepass():
    strength, verdict, _ = _rows(["failed", "passed"], ["passed", "passed"])
    assert (strength, verdict) == ("PARTIAL", "EVIDENCE_OK")


def test_invalid_when_only_broken():
    strength, verdict, _ = _rows(["failed"], ["failed"])
    assert (strength, verdict) == ("NONE", "INVALID_EVIDENCE")


def test_error_when_all_errored():
    strength, verdict, _ = _rows(["error"], ["error"])
    assert (strength, verdict) == ("NONE", "ERROR")


def test_error_when_no_rows():
    strength, verdict, rows = classify_run({}, {}, MutationResult())
    assert (strength, verdict, rows) == ("NONE", "ERROR", [])


# ---------------------------------------------------------------- junit parsing
def _write_junit(tmp_path, name, cases):
    body = "".join(
        f'<testcase classname="{cls}" name="{nm}">{inner}</testcase>'
        for cls, nm, inner in cases
    )
    (tmp_path / name).write_text(
        f'<testsuite name="s" tests="{len(cases)}">{body}</testsuite>'
    )


def test_parse_junit_outcomes(tmp_path):
    _write_junit(tmp_path, "base.xml", [
        ("m", "test_ok", ""),
        ("m", "test_fail", "<failure>boom</failure>"),
        ("m", "test_err", "<error>kaput</error>"),
    ])
    parsed = parse_junit(tmp_path / "base.xml")
    assert parsed == {
        "m::test_ok": "passed",
        "m::test_fail": "failed",
        "m::test_err": "error",
    }


def test_parse_junit_missing_file(tmp_path):
    assert parse_junit(tmp_path / "nope.xml") == {}


# ---------------------------------------------------------------- mutation
def test_parse_mutation_survivors_are_skipped(tmp_path):
    (tmp_path / "mutation.xml").write_text(
        "<testsuite>"
        '<testcase classname="mut1" name="1"/>'
        '<testcase classname="mut2" name="2"><skipped message="survived"/></testcase>'
        "</testsuite>"
    )
    mut = parse_mutation(tmp_path / "mutation.xml")
    assert (mut.status, mut.killed, mut.survived) == ("OK", 1, 1)
    assert mut.score == 0.5
    assert mut.survivor_samples == [
        {"mutant": "mut2::2", "detail": "survived"}
    ]


def test_parse_mutation_forced_status_never_scores(tmp_path):
    mut = parse_mutation(tmp_path / "whatever.xml", forced_status="TIMEOUT")
    assert (mut.status, mut.score) == ("TIMEOUT", None)


def test_parse_mutation_missing_file(tmp_path):
    mut = parse_mutation(tmp_path / "missing.xml")
    assert (mut.status, mut.score) == ("SKIPPED", None)


# ---------------------------------------------------------------- end-to-end evaluate
def test_evaluate_demo_fixture_shape(tmp_path):
    """Mirror the D2 demo outcome: 2 valid tests -> STRONG / EVIDENCE_OK."""
    _write_junit(tmp_path, "base.xml", [
        ("tests.test_a", "test_x", "<failure/>"),
        ("tests.test_a", "test_y", "<failure/>"),
    ])
    _write_junit(tmp_path, "pr.xml", [
        ("tests.test_a", "test_x", ""),
        ("tests.test_a", "test_y", ""),
    ])
    ev = evaluate(tmp_path, "aaa1111", "bbb2222", mut_status="OK")
    assert (ev.strength, ev.verdict) == ("STRONG", "EVIDENCE_OK")
    assert ev.counts == {"VALID": 2, "PRE-PASS": 0, "BROKEN": 0, "ERROR": 0}
    doc = ev.to_json()
    assert '"schema": "agent-acceptance/evidence@v0"' in doc
    assert '"base_sha": "aaa1111"' in doc


def test_evaluate_missing_runs_is_error(tmp_path):
    ev = evaluate(Path(tmp_path), "aaa", "bbb")
    assert (ev.strength, ev.verdict) == ("NONE", "ERROR")
