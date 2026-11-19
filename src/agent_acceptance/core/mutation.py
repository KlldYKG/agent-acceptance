"""Sampled mutation backend (SPEC v2 §3, component C2).

Engine is pinned: mutmut 2.5.x, driven through ``python -m mutmut``. The
procedure is exactly the frozen one:

1. generate mutmut config scoping mutation to the PR's changed source files
   (``S_Δ``) and the test runner to the changed tests (``T_Δ``);
2. ``mutmut run`` under a hard time budget (default 600 s);
3. ``mutmut junitxml`` -> killed/survived counts + up to 5 survivor samples.

Degradation rule (§3.3): budget exceeded, engine missing, or engine error
⇒ status SKIPPED/TIMEOUT with **no numeric claims** — a score is never
fabricated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agent_acceptance.core.evaluate import MutationResult, parse_mutation
from agent_acceptance.core.overlay import Changes, GitError, add_worktree, remove_worktree

JUNIT_TIMEOUT_S = 120


@dataclass
class MutationOutcome:
    result: MutationResult
    xml_path: Path | None = None


def write_mutmut_config(
    tree: Path, test_paths: list[str], src_paths: list[str], python_exe: str = "python"
) -> Path:
    """Write/merge the ``[mutmut]`` section of ``setup.cfg`` inside *tree*.

    Existing content outside ``[mutmut]`` (e.g. ``[tool:pytest]`` of the
    target project) is preserved verbatim. ``python_exe`` is interpolated
    into the runner *by absolute path*: mutmut executes the runner through a
    shell whose PATH does not necessarily carry the interpreter that has the
    target project (and pytest) installed — a bare ``python`` silently runs
    the baseline against the wrong environment and yields zero mutants.
    """
    cfg_path = tree / "setup.cfg"
    section = (
        "[mutmut]\n"
        f"runner={python_exe} -m pytest -x -q -p no:cacheprovider {' '.join(test_paths)}\n"
        f"paths_to_mutate={','.join(src_paths)}\n"
    )
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""

    if "[mutmut]" in text:
        head, _, _tail = text.partition("[mutmut]")
        text = head
    elif text and not text.endswith("\n"):
        text += "\n"
    cfg_path.write_text(text + section, encoding="utf-8")
    return cfg_path


def run_mutation(
    repo: Path,
    head: str,
    changes: Changes,
    out_dir: Path,
    budget_s: int,
    python_exe: str,
    work_root: Path | None = None,
) -> MutationOutcome:
    """Run the sampled-mutation phase; degrade gracefully, never fabricate."""
    out_dir = Path(out_dir)
    if not changes.srcs:
        return MutationOutcome(result=MutationResult(status="SKIPPED"))

    # Same relative-path trap as overlay.run_pytest (G11): the child cwd is a
    # worktree, so normalize the interpreter path up front. Crucially this is
    # *abspath*, not resolve(): venv ``bin/python`` is a symlink to the base
    # interpreter, and resolve() would silently retarget mutmut to the system
    # python — where the engine (and the target deps) are not installed.
    python_exe = os.path.abspath(python_exe)
    repo = Path(repo).resolve()
    mut_tree: Path | None = None
    tmp_work = None
    try:
        root = work_root if work_root is not None else Path(tempfile.mkdtemp(prefix="aa-mutation-"))
        if work_root is None:
            tmp_work = str(root)
        mut_tree = add_worktree(repo, head, root / "mut-tree")
        write_mutmut_config(mut_tree, changes.tests, changes.srcs, python_exe)

        try:
            subprocess.run(
                [python_exe, "-m", "mutmut", "run"],
                cwd=str(mut_tree),
                capture_output=True,
                text=True,
                timeout=budget_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return MutationOutcome(result=MutationResult(status="TIMEOUT"))
        except FileNotFoundError:
            # mutmut not importable by python_exe — engine unavailable.
            return MutationOutcome(result=MutationResult(status="SKIPPED"))

        junit = subprocess.run(
            [python_exe, "-m", "mutmut", "junitxml"],
            cwd=str(mut_tree),
            capture_output=True,
            text=True,
            timeout=JUNIT_TIMEOUT_S,
            check=False,
        )
        if junit.returncode != 0 or not junit.stdout.strip():
            return MutationOutcome(result=MutationResult(status="SKIPPED"))
        xml_path = out_dir / "mutation.xml"
        xml_path.write_text(junit.stdout, encoding="utf-8")
        return MutationOutcome(result=parse_mutation(xml_path), xml_path=xml_path)
    finally:
        if mut_tree is not None:
            try:
                remove_worktree(repo, mut_tree)
            except GitError:
                pass
        if tmp_work is not None:
            shutil.rmtree(tmp_work, ignore_errors=True)
