"""agent-acceptance CLI.

Subcommands (v1.0 scope, P1_CONSOLIDATION_PLAN output 2):
  run       Orchestrate base/PR overlay runs locally (git worktree backend).
  evaluate  Parse JUnit XML results and write evidence.json.
  report    Render the deterministic PR-comment Markdown from evidence.

Exit codes: see agent_acceptance.exit_codes.
Policy/config is loaded ONLY from the base branch of the target repository —
a pull request can never alter the checks applied to itself.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_acceptance import __version__
from agent_acceptance import exit_codes as xc
from agent_acceptance.core.evaluate import evaluate
from agent_acceptance.report.render import render


def _cmd_run(args: argparse.Namespace) -> int:
    from agent_acceptance.config import ConfigError, load_config
    from agent_acceptance.core.overlay import run_overlay

    repo = Path(args.repo).resolve()
    out_dir = Path(args.out)

    try:
        cfg = load_config(repo, args.base)
    except ConfigError as exc:
        print(f"error: bad .agent-acceptance.yml on base: {exc}", file=sys.stderr)
        return xc.USAGE
    except Exception as exc:  # bad ref etc.
        print(f"error: {exc}", file=sys.stderr)
        return xc.USAGE

    # CLI flags override the base policy where given.
    test_dirs = (
        [d for d in args.test_dirs.split(",") if d.strip()]
        if args.test_dirs is not None else cfg.test_dirs
    )
    budget = args.mutation_budget if args.mutation_budget is not None \
        else cfg.mutation_budget_seconds
    flaky_n = args.flaky_n if args.flaky_n is not None else cfg.flaky_n
    gate = args.gate_flag if args.gate_flag is not None else cfg.gate
    # SPEC §2.5: flaky repeats are a gate-mode-only extension; report mode
    # always runs each side once.
    if not (gate and not args.report_only):
        flaky_n = 1

    try:
        outcome = run_overlay(
            repo, args.base, args.head, out_dir,
            test_dirs=test_dirs, python_exe=args.python,
            flaky_n=flaky_n, mutation_budget_s=budget,
        )
    except Exception as exc:  # noqa: BLE001 — surface as usage/infra error
        print(f"error: {exc}", file=sys.stderr)
        return xc.USAGE

    if outcome is None:
        (out_dir / "report.md").write_text(
            "# Test-evidence assessment\n\n"
            "No test files were changed in this PR, so there is no executable "
            "test evidence to verify.\n"
        )
        print("verdict=NO_EVIDENCE (no changed test files)")
        return xc.OK

    ev = outcome.evidence
    print(f"strength={ev.strength} verdict={ev.verdict}")
    print(f"evidence: {out_dir / 'evidence.json'}")

    if gate and not args.report_only:
        if ev.verdict in ("INVALID_EVIDENCE", "ERROR"):
            # No usable evidence at all (incl. collection failures) -> invalid.
            return xc.GATE_INVALID_EVIDENCE
        if any(t.verdict == "BROKEN" for t in ev.tests):
            return xc.GATE_BROKEN
    return xc.OK


def _cmd_evaluate(args: argparse.Namespace) -> int:
    ev = evaluate(
        Path(args.out_dir), args.base_sha, args.head_sha,
        mut_status=args.mutation_status,
    )
    (Path(args.out_dir) / "evidence.json").write_text(ev.to_json())
    print(f"strength={ev.strength} verdict={ev.verdict}")
    return xc.OK


def _cmd_report(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    ev = evaluate(out, args.base_sha, args.head_sha,
                  mut_status=args.mutation_status) \
        if not (out / "evidence.json").exists() else _load_evidence(out)
    (out / "report.md").write_text(render(ev))
    print(f"wrote {out / 'report.md'}")
    return xc.OK


def _load_evidence(out: Path):
    import json

    from agent_acceptance.core.evaluate import Evidence, MutationResult, TestRow

    doc = json.loads((out / "evidence.json").read_text())
    return Evidence(
        base_sha=doc["base_sha"],
        head_sha=doc["head_sha"],
        strength=doc["strength"],
        verdict=doc["verdict"],
        tests=[
            TestRow(key=t["test"], on_base=t["on_base"], on_pr=t["on_pr"],
                    verdict=t["verdict"], note=t.get("note", ""))
            for t in doc["tests"]
        ],
        mutation=MutationResult(**doc["mutation"]),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-acceptance", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    ev = sub.add_parser("evaluate", help="write evidence.json from JUnit XML runs")
    ev.add_argument("out_dir", help="directory containing base.xml / pr.xml [/mutation.xml]")
    ev.add_argument("--base-sha", required=True)
    ev.add_argument("--head-sha", required=True)
    ev.add_argument("--mutation-status", default=None,
                    help="force mutation phase status (e.g. TIMEOUT -> SKIPPED, no score)")
    ev.set_defaults(func=_cmd_evaluate)

    rp = sub.add_parser("report", help="render report.md (from evidence.json or raw runs)")
    rp.add_argument("out_dir")
    rp.add_argument("--base-sha", default="")
    rp.add_argument("--head-sha", default="")
    rp.add_argument("--mutation-status", default=None)
    rp.set_defaults(func=_cmd_report)

    rn = sub.add_parser("run", help="orchestrate base/PR overlay runs locally")
    rn.add_argument("--repo", default=".", help="path to the target git repository")
    rn.add_argument("--base", required=True, help="base revision (SHA/branch/tag)")
    rn.add_argument("--head", required=True, help="PR revision (SHA/branch/tag)")
    rn.add_argument("--out", default="out", help="output directory for XML/evidence")
    rn.add_argument("--test-dirs", default=None,
                    help="comma-separated test dirs (default: policy on base)")
    rn.add_argument("--python", default=sys.executable,
                    help="python interpreter used to run pytest/mutmut")
    rn.add_argument("--mutation-budget", type=int, default=None, metavar="S",
                    help="mutation time budget in seconds (default: policy on "
                         "base, 600); 0 disables the mutation phase")
    rn.add_argument("--flaky-n", type=int, default=None, metavar="N",
                    help="runs per phase; >1 enables the flaky rule "
                         "(default: policy on base, 3; report mode may use 1)")
    rn.add_argument("--gate", dest="gate_flag", action="store_true", default=None,
                    help="gate mode: exit non-zero on invalid evidence "
                         "(default: policy on base, off)")
    rn.add_argument("--no-gate", dest="gate_flag", action="store_false",
                    help="force report mode regardless of policy")
    rn.add_argument("--report-only", action="store_true",
                    help="never convert findings into a non-zero exit code")
    rn.set_defaults(func=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
