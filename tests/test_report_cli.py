"""Tests for the deterministic report renderer + CLI exit codes."""

import json

from agent_acceptance import exit_codes as xc
from agent_acceptance.cli import main
from agent_acceptance.core.evaluate import Evidence, MutationResult, TestRow
from agent_acceptance.report.render import MARKER, render


def _ev(strength="STRONG", verdict="EVIDENCE_OK", mut=None):
    return Evidence(
        base_sha="aaa11112222",
        head_sha="bbb33334444",
        strength=strength,
        verdict=verdict,
        tests=[
            TestRow("tests.test_a::test_x", "failed", "passed", "VALID"),
            TestRow("tests.test_b::test_y", "passed", "passed", "PRE-PASS"),
        ],
        mutation=mut or MutationResult(status="SKIPPED"),
    )


# ---------------------------------------------------------------- render
def test_render_contains_strength_table_and_marker():
    out = render(_ev())
    assert out.startswith(MARKER)
    assert "**Strength: STRONG**" in out
    assert "`tests.test_a::test_x`" in out
    assert "**VALID** ✅" in out and "PRE-PASS ⚠️" in out
    assert "no LLM involved" in out


def test_render_mutation_score():
    mut = MutationResult(status="OK", killed=3, survived=1, score=0.75,
                         survivor_samples=[{"mutant": "m::1", "detail": "x"}])
    out = render(_ev(mut=mut))
    assert "3/4 (75%)" in out and "`m::1`" in out


def test_render_mutation_skipped_has_no_numbers():
    out = render(_ev())
    assert "SKIPPED" in out and "/4" not in out


def test_render_is_deterministic():
    assert render(_ev()) == render(_ev())


def test_render_no_evidence():
    ev = Evidence("a", "b", "NONE", "NO_EVIDENCE", [], MutationResult())
    out = render(ev)
    assert "no executable test evidence" in out and "|---|" not in out


# ---------------------------------------------------------------- CLI
def test_cli_evaluate_writes_evidence(tmp_path, capsys):
    junit = (
        '<testsuite tests="1">'
        '<testcase classname="t" name="a"><failure/></testcase></testsuite>'
    )
    (tmp_path / "base.xml").write_text(junit)
    (tmp_path / "pr.xml").write_text(
        '<testsuite tests="1"><testcase classname="t" name="a"/></testsuite>'
    )
    rc = main(["evaluate", str(tmp_path), "--base-sha", "aaa", "--head-sha", "bbb"])
    assert rc == xc.OK
    doc = json.loads((tmp_path / "evidence.json").read_text())
    assert doc["strength"] == "STRONG"
    assert "strength=STRONG" in capsys.readouterr().out


def test_cli_report_reads_existing_evidence(tmp_path):
    ev = _ev()
    (tmp_path / "evidence.json").write_text(ev.to_json())
    rc = main(["report", str(tmp_path)])
    assert rc == xc.OK
    assert (tmp_path / "report.md").read_text().startswith(MARKER)


def test_cli_run_not_implemented_exit_2(capsys):
    rc = main(["run"])
    assert rc == xc.USAGE
    assert "not implemented" in capsys.readouterr().err
