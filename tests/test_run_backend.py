"""Tests for the local worktree backend (core/overlay.py + `run` subcommand).

Fixtures build a miniature version of the demo repository: base has a buggy
function and a test that exposes the bug (fails), the PR fixes the function
(the same test passes) — the canonical fail-before / pass-after pair.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_acceptance import exit_codes as xc
from agent_acceptance.cli import main
from agent_acceptance.core.overlay import (
    PytestRunnerError,
    classify_changes,
    is_test_file,
    run_overlay,
    run_pytest,
)

SRC_BASE = "def add(a, b):\n    return a - b\n"  # bug: subtracts
SRC_FIX = "def add(a, b):\n    return a + b\n"
TEST = "from src.add import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def demo_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "demo repo"  # space on purpose: paths-with-spaces guard
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")

    (repo / "src" / "add.py").write_text(SRC_BASE)
    (repo / "src" / "__init__.py").write_text("")
    (repo / "tests" / "test_other.py").write_text("def test_other():\n    assert 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    _git(repo, "checkout", "-qb", "fix")
    (repo / "src" / "add.py").write_text(SRC_FIX)
    (repo / "tests" / "test_add.py").write_text(TEST)  # the PR adds the evidence
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fix add() + test")
    return repo


# ---------------------------------------------------------------- unit level


def test_is_test_file_matrix() -> None:
    dirs = ["tests", "src/tests"]
    assert is_test_file("tests/test_a.py", dirs)
    assert is_test_file("tests/test_a_test.py", dirs)
    assert is_test_file("src/tests/test_b.py", dirs)
    assert not is_test_file("other/test_c.py", dirs)
    assert not is_test_file("tests/helper.py", dirs)
    assert not is_test_file("tests/test_c.txt", dirs)


def test_classify_changes(demo_repo: Path) -> None:
    changes = classify_changes(demo_repo, "main", "fix", ["tests"])
    assert changes.tests == ["tests/test_add.py"]
    assert changes.srcs == ["src/add.py"]


# ------------------------------------------------------- runner diagnostics


def test_run_pytest_relative_interpreter(demo_repo: Path, tmp_path: Path) -> None:
    """G11 regression: a relative --python path must survive the cwd switch."""
    import os
    import sys

    rel_exe = os.path.relpath(sys.executable, os.getcwd())
    xml = tmp_path / "rel.xml"
    run_pytest(demo_repo, ["tests/test_other.py"], xml, python_exe=rel_exe)
    assert xml.exists() and xml.stat().st_size > 0


def test_run_pytest_bad_interpreter_raises(tmp_path: Path) -> None:
    """G11 regression: a dead interpreter must fail loudly, not silently."""
    xml = tmp_path / "out.xml"
    with pytest.raises(PytestRunnerError, match="cannot launch pytest interpreter"):
        run_pytest(tmp_path, ["tests/test_x.py"], xml, python_exe=str(tmp_path / "no-such-python"))


# --------------------------------------------------------------- end to end


def test_run_overlay_valid(demo_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    outcome = run_overlay(demo_repo, "main", "fix", out, work_root=tmp_path / "wt")

    assert outcome is not None
    ev = outcome.evidence
    assert ev.verdict == "EVIDENCE_OK"
    assert ev.strength == "STRONG"
    assert ev.tests[0].verdict == "VALID"
    assert (out / "evidence.json").exists()
    assert (out / "base.xml").exists() and (out / "pr.xml").exists()
    # worktrees cleaned up
    assert not (tmp_path / "wt" / "base-tree").exists()


def test_run_overlay_no_tests(tmp_path: Path) -> None:
    repo = tmp_path / "docs repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "docs")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs")

    assert run_overlay(repo, "main", "docs", tmp_path / "out") is None


def test_run_overlay_prepass(tmp_path: Path) -> None:
    """A test that already passes on base is PRE-PASS (proves nothing)."""
    repo = tmp_path / "prepass"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "add.py").write_text(SRC_FIX)  # correct on base already
    (repo / "src" / "__init__.py").write_text("")
    (repo / "tests" / "test_other.py").write_text("def test_other():\n    assert 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "pr")
    (repo / "tests" / "test_add.py").write_text(TEST)  # new test, passes on both
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add test only")

    outcome = run_overlay(repo, "main", "pr", tmp_path / "out", work_root=tmp_path / "wt")
    assert outcome is not None
    assert outcome.evidence.verdict == "INVALID_EVIDENCE"
    assert outcome.evidence.tests[0].verdict == "PRE-PASS"


# ------------------------------------------------------------------ CLI


def test_cli_run_subcommand(demo_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli-out"
    rc = main(
        [
            "run",
            "--repo",
            str(demo_repo),
            "--base",
            "main",
            "--head",
            "fix",
            "--out",
            str(out),
        ]
    )
    assert rc == xc.OK
    assert (out / "evidence.json").exists()
    assert "EVIDENCE_OK" in (out / "evidence.json").read_text()


def test_cli_run_bad_revision(demo_repo: Path, tmp_path: Path) -> None:
    rc = main(
        [
            "run",
            "--repo",
            str(demo_repo),
            "--base",
            "no-such-ref",
            "--head",
            "fix",
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert rc == xc.USAGE
