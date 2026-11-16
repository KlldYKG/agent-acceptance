"""Tests for G3: base-branch policy config + sampled mutation backend."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_acceptance import exit_codes as xc
from agent_acceptance.cli import main
from agent_acceptance.config import Config, ConfigError, load_config, parse_config
from agent_acceptance.core.mutation import write_mutmut_config
from agent_acceptance.core.overlay import Changes, GitError, run_overlay

# ---------------------------------------------------------------- config

def test_parse_config_defaults_on_empty() -> None:
    cfg = parse_config("")
    assert cfg == Config()


def test_parse_config_full_schema() -> None:
    text = (
        "# policy\n"
        "test-dirs: tests, src/tests  # two dirs\n"
        "mutation-budget-seconds: 120\n"
        "flaky-n: 2\n"
        "gate: true\n"
        "unknown-future-key: whatever\n"
    )
    cfg = parse_config(text)
    assert cfg.test_dirs == ["tests", "src/tests"]
    assert cfg.mutation_budget_seconds == 120
    assert cfg.flaky_n == 2
    assert cfg.gate is True


def test_parse_config_rejects_malformed() -> None:
    with pytest.raises(ConfigError):
        parse_config("this line has no colon")


def test_parse_config_rejects_bad_int() -> None:
    with pytest.raises(ConfigError):
        parse_config("flaky-n: many")


def test_load_config_missing_file_gives_defaults(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")

    assert load_config(repo, "main") == Config()


def test_load_config_reads_from_ref_not_worktree(tmp_path: Path) -> None:
    """The policy comes from the committed base revision, never the worktree."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "-qm", "base2", "--allow-empty")
    (repo / ".agent-acceptance.yml").write_text("gate: true\nflaky-n: 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "policy")
    # Uncommitted local change must be invisible:
    (repo / ".agent-acceptance.yml").write_text("gate: false\n")

    cfg = load_config(repo, "main")
    assert cfg.gate is True
    assert cfg.flaky_n == 2


def test_load_config_bad_ref_raises(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "-qm", "base", "--allow-empty")
    with pytest.raises(GitError):
        load_config(repo, "no-such-ref")


# --------------------------------------------------------------- mutation

def test_write_mutmut_config_fresh_tree(tmp_path: Path) -> None:
    path = write_mutmut_config(tmp_path, ["tests/test_a.py"], ["src/a.py"])
    text = path.read_text()
    assert "[mutmut]" in text
    assert "paths_to_mutate=src/a.py" in text
    assert "tests/test_a.py" in text


def test_write_mutmut_config_preserves_existing_sections(tmp_path: Path) -> None:
    cfg = tmp_path / "setup.cfg"
    cfg.write_text("[tool:pytest]\naddopts = -q\n\n[mutmut]\nrunner=old\n")
    write_mutmut_config(tmp_path, ["tests/t.py"], ["src/a.py"])
    text = cfg.read_text()
    assert "[tool:pytest]" in text
    assert "addopts = -q" in text
    assert "runner=old" not in text  # replaced wholesale
    assert "paths_to_mutate=src/a.py" in text


def test_run_mutation_no_src_changes_skips(tmp_path: Path) -> None:
    """No changed source -> phase skipped before any repo access; no score."""
    from agent_acceptance.core.mutation import run_mutation

    outcome = run_mutation(
        repo=None, head="x",
        changes=Changes(tests=["tests/t.py"], srcs=[]),
        out_dir=tmp_path, budget_s=600, python_exe="unused",
    )
    assert outcome.result.status == "SKIPPED"
    assert outcome.result.score is None  # never fabricate


# --------------------------------------------------- overlay integration

FIX_CFG = ".agent-acceptance.yml"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def mut_repo(tmp_path: Path) -> Path:
    """Repo whose PR changes src + adds a test (mutation phase applicable)."""
    repo = tmp_path / "mutrepo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src" / "add.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "src" / "__init__.py").write_text("")
    (repo / "tests" / "test_other.py").write_text("def test_other():\n    assert 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "fix")
    (repo / "src" / "add.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "tests" / "test_add.py").write_text(
        "from src.add import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fix+test")
    return repo


def test_run_overlay_mutation_skipped_without_mutmut(
    mut_repo: Path, tmp_path: Path
) -> None:
    """mutmut is not installed in the test env -> SKIPPED, verdict intact."""
    out = tmp_path / "out"
    outcome = run_overlay(
        mut_repo, "main", "fix", out,
        work_root=tmp_path / "wt", mutation_budget_s=600,
    )
    assert outcome is not None
    ev = outcome.evidence
    assert ev.verdict == "EVIDENCE_OK"
    assert ev.mutation.status == "SKIPPED"
    assert ev.mutation.score is None


# -------------------------------------------------------------- CLI/gate

GATE_CFG = "gate: true\nmutation-budget-seconds: 0\n"


@pytest.fixture
def gate_repo(mut_repo: Path) -> Path:
    _git(mut_repo, "checkout", "-q", "main")
    (mut_repo / FIX_CFG).write_text(GATE_CFG)
    _git(mut_repo, "add", "-A")
    _git(mut_repo, "commit", "-qm", "policy: gate on")
    _git(mut_repo, "checkout", "-q", "fix")
    return mut_repo


def test_cli_gate_mode_invalid_evidence_exit_10(mut_repo, tmp_path: Path) -> None:
    """PRE-PASS-only evidence under gate mode -> exit 10."""
    repo = mut_repo
    _git(repo, "checkout", "-q", "main")
    # policy commit on main; then a PR that only ADDS a passing test
    (repo / FIX_CFG).write_text(GATE_CFG)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "policy: gate on")
    _git(repo, "checkout", "-qb", "slop")
    (repo / "tests" / "test_add.py").write_text(
        "from src.add import add\n\ndef test_add():\n    assert add(2, 2) == 4\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pre-pass test")

    rc = main([
        "run", "--repo", str(repo), "--base", "main", "--head", "slop",
        "--out", str(tmp_path / "out"),
    ])
    assert rc == xc.GATE_INVALID_EVIDENCE


def test_cli_report_mode_slop_exits_zero(mut_repo, tmp_path: Path) -> None:
    """Same slop PR, explicit --no-gate: report only, exit 0."""
    repo = mut_repo
    _git(repo, "checkout", "-q", "main")
    (repo / FIX_CFG).write_text(GATE_CFG)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "policy: gate on")
    _git(repo, "checkout", "-qb", "slop2")
    (repo / "tests" / "test_add.py").write_text(
        "from src.add import add\n\ndef test_add():\n    assert add(2, 2) == 4\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pre-pass test")

    rc = main([
        "run", "--repo", str(repo), "--base", "main", "--head", "slop2",
        "--out", str(tmp_path / "out2"), "--no-gate",
    ])
    assert rc == xc.OK
