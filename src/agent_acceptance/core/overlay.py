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
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agent_acceptance.core.evaluate import Evidence, evaluate, parse_junit


class GitError(RuntimeError):
    """A git command failed while preparing the overlay runs."""


class PytestRunnerError(RuntimeError):
    """pytest could not run at all (infrastructure problem, not test data).

    A pytest process that *starts* always produces JUnit XML — even when every
    test fails or the collection explodes. Missing/empty XML therefore means
    the interpreter never got off the ground (bad path, wrong cwd, import
    crash before the plugin hook); that must surface loudly, not degrade into
    a silent ERROR verdict with zero diagnostics.
    """


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
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
    return any(path == d.rstrip("/") or path.startswith(d.rstrip("/") + "/") for d in test_dirs)


@dataclass
class Changes:
    tests: list[str]
    srcs: list[str]


def classify_changes(repo: Path, base: str, head: str, test_dirs: list[str]) -> Changes:
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


class PhaseTimeout(RuntimeError):
    """A phase exceeded its wall-clock budget (e.g. a hang/deadloop test).

    This is *data about the run*, not an infrastructure crash: the phase is
    recorded as a synthetic errored testcase so the verdict degrades to
    ERROR instead of the gate hanging forever inside CI.
    """


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Kill *proc* and (on POSIX) its whole process group.

    pytest spawns children (xdist workers, plugin subprocesses); killing only
    the direct child leaks them (observed: an orphaned pytest surviving an
    outer harness kill for over an hour).
    """
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            pass
    proc.kill()


def _write_phase_timeout_junit(path: Path) -> None:
    """Write a one-case JUnit XML marking the phase as timed out.

    ``parse_junit`` turns the ``<error>`` child into outcome ``error``, which
    ``classify_run`` aggregates into an ERROR/NONE verdict — an honest
    "could not verify" instead of a fabricated pass.
    """
    suite = ET.Element("testsuite", tests="1")
    case = ET.SubElement(suite, "testcase", classname="__gate__", name="phase_timeout")
    ET.SubElement(case, "error", message="phase exceeded the wall-clock timeout")
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=False)


def run_pytest(
    tree: Path,
    test_paths: list[str],
    junit_xml: Path,
    python_exe: str = sys.executable,
    timeout_s: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run the changed tests inside *tree*; a failing run is data, not an error."""
    # Resolve the interpreter against the invoker's cwd *before* the child
    # switches its own cwd to the worktree. A relative --python path used to
    # evaporate here (G11): pytest silently never ran and the run degraded
    # into an ERROR verdict with no diagnostics.
    exe = str(Path(python_exe).resolve())
    cmd = [
        exe,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-q",
        f"--junitxml={junit_xml}",
        *test_paths,
    ]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # A stale JUnit file from a previous invocation must not mask a timeout.
    if junit_xml.exists():
        junit_xml.unlink()
    # start_new_session detaches the child into its own process group so a
    # timeout can kill the entire tree (pytest + workers), not just the shell.
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(tree),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise PytestRunnerError(f"cannot launch pytest interpreter {exe}: {exc}") from exc
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout_s or None)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        try:
            out, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out = ""
    if timed_out:
        # A killed pytest leaves no (or a truncated) JUnit file — record the
        # timeout explicitly rather than misreporting it as "failed to start".
        _write_phase_timeout_junit(junit_xml)
        raise PhaseTimeout(
            f"pytest phase on {tree.name} exceeded {timeout_s}s wall clock; "
            f"process group terminated. output tail: {(out or '')[-600:]}"
        )
    if not junit_xml.exists() or junit_xml.stat().st_size == 0:
        tail = (out or "").strip()
        tail = tail[-600:] if tail else "(pytest produced no output)"
        raise PytestRunnerError(
            f"pytest wrote no JUnit XML for the {tree.name} tree "
            f"(interpreter: {exe}); it likely failed to start or import. "
            f"output tail: {tail}"
        )
    return subprocess.CompletedProcess(cmd, proc.returncode, out, "")


def _write_synthetic_junit(path: Path, outcomes: dict[str, str]) -> None:
    """Serialise merged per-test outcomes back to JUnit XML (flaky repeats)."""
    suite = ET.Element("testsuite", tests=str(len(outcomes)))
    for key in sorted(outcomes):
        classname, _, name = key.partition("::")
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        outcome = outcomes[key]
        if outcome == "failed":
            ET.SubElement(case, "failure", message="failed")
        elif outcome == "error":
            ET.SubElement(case, "error", message="unstable across repeats")
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=False)


def run_phase_repeat(
    tree: Path,
    test_paths: list[str],
    out_dir: Path,
    name: str,
    repeats: int,
    python_exe: str = sys.executable,
    timeout_s: int = 0,
) -> dict[str, str]:
    """Run one phase up to *repeats* times (SPEC §2.5 flaky handling).

    With ``repeats == 1`` this is a single plain run. With ``repeats > 1``
    (gate-mode extension, ``flaky-n`` config), every test must show the same
    outcome in every run; an unstable test maps to ``error`` — flakiness is
    never evidence. ``timeout_s`` bounds each pytest invocation; 0 disables
    the wall-clock limit.
    """
    runs: list[dict[str, str]] = []
    canonical = out_dir / f"{name}.xml"
    for i in range(max(1, repeats)):
        xml = out_dir / f"{name}.{i}.xml" if repeats > 1 else canonical
        try:
            run_pytest(tree, test_paths, xml, python_exe, timeout_s=timeout_s)
        except PhaseTimeout:
            # A phase that hangs once will hang again; the synthetic marker
            # case carries the timeout into evaluation and degrades the run.
            return parse_junit(xml)
        runs.append(parse_junit(xml))

    if repeats <= 1:
        return runs[0]

    merged: dict[str, str] = {}
    keys: set[str] = set()
    for r in runs:
        keys.update(r)
    for key in keys:
        outcomes = {r.get(key, "missing") for r in runs}
        merged[key] = outcomes.pop() if len(outcomes) == 1 else "error"
    _write_synthetic_junit(canonical, merged)
    return merged


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
    flaky_n: int = 1,
    mutation_budget_s: int = 0,
    phase_timeout_s: int = 0,
) -> RunOutcome | None:
    """Orchestrate the double run (+ optional mutation phase) and evaluate.

    Returns None when the PR changes no test files (nothing to verify — the
    caller renders the NO_EVIDENCE report).

    ``flaky_n``: repeats per phase; >1 engages the gate-mode flaky rule
    (unstable tests count as ``error``). ``mutation_budget_s``: 0 skips the
    mutation phase entirely; otherwise SPEC §3 budget semantics apply and a
    budget overrun degrades to TIMEOUT/SKIPPED (never a fabricated score).
    ``phase_timeout_s``: wall-clock limit per pytest phase; a phase that
    exceeds it is recorded as an errored marker case (verdict degrades to
    ERROR) instead of the gate hanging forever.
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
        root = work_root if work_root is not None else Path(tempfile.mkdtemp(prefix="aa-overlay-"))
        if work_root is None:
            tmp_work = str(root)
        base_tree = add_worktree(repo, base, root / "base-tree")
        head_tree = add_worktree(repo, head, root / "pr-tree")
        # New tests against the OLD source (fail-before).
        overlay_files(head_tree, base_tree, changes.tests)

        run_phase_repeat(base_tree, changes.tests, out_dir, "base", flaky_n,
                         python_exe, timeout_s=phase_timeout_s)
        run_phase_repeat(head_tree, changes.tests, out_dir, "pr", flaky_n,
                         python_exe, timeout_s=phase_timeout_s)
    finally:
        for tree in (base_tree, head_tree):
            if tree is not None:
                try:
                    remove_worktree(repo, tree)
                except GitError:
                    pass
        if tmp_work is not None:
            shutil.rmtree(tmp_work, ignore_errors=True)

    mut_status = "SKIPPED"
    if mutation_budget_s > 0 and changes.srcs:
        from agent_acceptance.core.mutation import run_mutation

        outcome = run_mutation(
            repo, head, changes, out_dir, mutation_budget_s, python_exe, work_root
        )
        mut_status = outcome.result.status

    ev = evaluate(out_dir, base, head, mut_status=mut_status)
    (out_dir / "evidence.json").write_text(ev.to_json(), encoding="utf-8")
    return RunOutcome(
        changes=changes, base_xml=out_dir / "base.xml", pr_xml=out_dir / "pr.xml", evidence=ev
    )
