"""agent-acceptance CLI.

Subcommands (v1.0 scope, P1_CONSOLIDATION_PLAN output 2):
  evaluate  Parse JUnit XML results and write evidence.json.
  report    Render the deterministic PR-comment Markdown from evidence.
  run       (planned) orchestrate overlay runs locally via git worktree.

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


def _cmd_run(_args: argparse.Namespace) -> int:
    print(
        "error: `run` (local worktree orchestration) is not implemented in this "
        "developer preview; use the GitHub Action, which is the validated path.",
        file=sys.stderr,
    )
    return xc.USAGE


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

    rn = sub.add_parser("run", help="orchestrate overlay runs locally (planned)")
    rn.set_defaults(func=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
