"""Local worktree backend: prepare base/PR trees, run changed tests, evaluate.

CLI counterpart of the Action's bash orchestration
(demo/tool/action/scripts/run.sh), replacing `git archive | tar` with
`git worktree add --detach` and Docker sandboxing with a plain subprocess:

- base phase: pristine base tree + the PR's changed test files overlaid on
  top -> the new tests run against the OLD source (fail-before).
- pr phase: the PR tree checked out as-is -> the same tests against the NEW
  source (pass-after).

All subprocess invocations use argv lists (never a shell string), so paths
containing spaces are safe. Failing pytest runs are *data*, not runner
errors, so the exit status of pytest is deliberately ignored.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agent_acceptance.core.evaluate import Evidence, evaluate


class GitError(RuntimeError):
    """A git command failed while preparing the overlay runs."""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    """Files changed between two revisions (POSIX-style relative paths)."""
    out = _git(repo, "diff", "--name-only", base, head)
    return [line for line in out.splitlines() if line.strip()]


def is_test_file(path: str, test_dirs: list[str]) -> bool:
    """True for *.py files under a test dir named test_*.py or *_test.py."""
    if not path.endswith(".py"):
        return False
    name = PurePosixPath(path).name
    if not (name.startswith("test_") or name.endswith("_test.py")):
        return False
    return any(
        path == d.rstrip("/") or path.startswith(d.rstrip("/") + "/")
        for d in test_dirs
    )


@dataclass
class Changes:
    tests: list[str]
    srcs: list[str]


def classify_changes(
    repo: Path, base: str, head: str, test_dirs: list[str]
) -> Changes:
    """Split changed files into test files and other Python source files."""
    tests: list[str] = []
    srcs: list[str] = []
    for path in changed_files(repo, base, head):
        if is_test_file(path, test_dirs):
            tests.append(path)
        elif path.endswith(".py"):
            srcs.append(path)
    return Changes(tests=tests, srcs=srcs)


def add_worktree(repo: Path, ref: str, dest: Path) -> Path:
    """Materialise *ref* into *dest* via a detached worktree."""
    _git(repo, "worktree", "add", "--detach", str(dest), ref)
    return dest


def remove_worktree(repo: Path, dest: Path) -> None:
    _git(repo, "worktree", "remove", "--force", str(dest))


def overlay_files(src_tree: Path, dest_tree: Path, rel_paths: list[str]) -> None:
    """Copy *rel_paths* from src_tree onto dest_tree (plain copy, no execution)."""
    for rel in rel_paths:
        src = src_tree.joinpath(*PurePosixPath(rel).parts)
        dest = dest_tree.joinpath(*PurePosixPath(rel).parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def run_pytest(
    tree: Path,
    test_paths: list[str],
    junit_xml: Path,
    python_exe: str = sys.executable,
) -> subprocess.CompletedProcess[str]:
    """Run the changed tests inside *tree*; a failing run is data, not an error."""
    cmd = [
        python_exe, "-m", "pytest", "-p", "no:cacheprovider", "-q",
        f"--junitxml={junit_xml}",
        *test_paths,
    ]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        cmd, cwd=str(tree), env=env, capture_output=True, text=True, check=False
    )


@dataclass
class RunOutcome:
    """Result of one overlay orchestration."""

    changes: Changes
    base_xml: Path
    pr_xml: Path
    evidence: Evidence


def run_overlay(
    repo: Path,
    base: str,
    head: str,
    out_dir: Path,
    test_dirs: list[str] | None = None,
    python_exe: str = sys.executable,
    work_root: Path | None = None,
    mut_status: str | None = "SKIPPED",
) -> RunOutcome | None:
    """Orchestrate the double run and evaluate.

    Returns None when the PR changes no test files (nothing to verify — the
    caller renders the NO_EVIDENCE report). Mutation runs belong to the
    mutation backend (G3); until it lands the phase is reported SKIPPED.
    """
    repo = Path(repo).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    test_dirs = test_dirs if test_dirs is not None else ["tests"]

    changes = classify_changes(repo, base, head, test_dirs)
    if not changes.tests:
        return None

    base_tree: Path | None = None
    head_tree: Path | None = None
    tmp_work: str | None = None
    try:
        # Worktrees must live OUTSIDE the repository working tree (git refuses
        # nested worktrees), so the default root is a fresh temp directory.
        root = work_root if work_root is not None else Path(
            tempfile.mkdtemp(prefix="aa-overlay-")
        )
        if work_root is None:
            tmp_work = str(root)
        base_tree = add_worktree(repo, base, root / "base-tree")
        head_tree = add_worktree(repo, head, root / "pr-tree")
        # New tests against the OLD source (fail-before).
        overlay_files(head_tree, base_tree, changes.tests)

        base_xml = out_dir / "base.xml"
        pr_xml = out_dir / "pr.xml"
        run_pytest(base_tree, changes.tests, base_xml, python_exe)
        run_pytest(head_tree, changes.tests, pr_xml, python_exe)
    finally:
        for tree in (base_tree, head_tree):
            if tree is not None:
                try:
                    remove_worktree(repo, tree)
                except GitError:
                    pass
        if tmp_work is not None:
            shutil.rmtree(tmp_work, ignore_errors=True)

    ev = evaluate(out_dir, base, head, mut_status=mut_status)
    (out_dir / "evidence.json").write_text(ev.to_json())
    return RunOutcome(changes=changes, base_xml=base_xml, pr_xml=pr_xml, evidence=ev)
