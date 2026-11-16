"""Repository policy, loaded ONLY from the base revision.

A pull request can never alter the checks applied to itself: the config
file (``.agent-acceptance.yml``) is read from the base ref via ``git show``
— never from the working tree and never from the PR.

Supported flat schema (scalars only; no nesting, no anchors):

    test-dirs: tests,src/tests        # comma-separated
    mutation-budget-seconds: 600
    flaky-n: 3
    gate: false

Unknown keys are ignored (forward compatibility). Malformed lines raise
ConfigError. The parser is a deliberately minimal flat-YAML subset so the
core stays dependency-free and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_acceptance.core.overlay import GitError, _git


class ConfigError(ValueError):
    """The policy file exists but cannot be parsed."""


@dataclass
class Config:
    test_dirs: list[str] = field(default_factory=lambda: ["tests"])
    mutation_budget_seconds: int = 600
    flaky_n: int = 3
    gate: bool = False


def _coerce_bool(value: str) -> bool:
    low = value.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    raise ConfigError(f"expected boolean, got {value!r}")


def _coerce_int(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ConfigError(f"expected integer, got {value!r}") from exc


def parse_config(text: str) -> Config:
    cfg = Config()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        line = raw.split(" #", 1)[0].rstrip()  # strip trailing comments
        if not line.strip():
            continue
        if ":" not in line:
            raise ConfigError(f"line {lineno}: expected 'key: value', got {raw!r}")
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "test-dirs":
            cfg.test_dirs = [d.strip() for d in value.split(",") if d.strip()] or ["tests"]
        elif key == "mutation-budget-seconds":
            cfg.mutation_budget_seconds = _coerce_int(value)
        elif key == "flaky-n":
            cfg.flaky_n = _coerce_int(value)
        elif key == "gate":
            cfg.gate = _coerce_bool(value)
        # unknown keys ignored (forward compatibility)
    return cfg


def load_config(repo, ref: str) -> Config:
    """Read .agent-acceptance.yml from *ref*; missing file -> defaults.

    Raises GitError when *ref* itself cannot be resolved; returns defaults
    when the ref is fine but the policy file is absent.
    """
    _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    try:
        text = _git(repo, "show", f"{ref}:.agent-acceptance.yml")
    except GitError:
        return Config()
    return parse_config(text)
