#!/usr/bin/env python3
"""
check_docs.py — matrix-aware documentation update guard.

Reads `/app/docs/doc_rules.yml` and fails whenever a PR (or local commit)
touches files matched by a rule's `when` globs without also updating every
doc listed under that rule's `require`.

Usage:
    scripts/check_docs.py [base_ref]

    base_ref auto-detection (when omitted):
        1. $GITHUB_BASE_REF (set by GitHub Actions on pull_request)
        2. origin/main
        3. HEAD~1

Exit codes:
    0  all rules satisfied
    1  one or more rules violated
    2  usage / environment error
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("check_docs: PyYAML is required — pip install pyyaml", file=sys.stderr)
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = REPO_ROOT / "docs" / "doc_rules.yml"

ANSI_BOLD = "\033[1m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RESET = "\033[0m"


def _color(text: str, code: str) -> str:
    if not sys.stdout.isatty() and not os.environ.get("FORCE_COLOR"):
        return text
    return f"{code}{text}{ANSI_RESET}"


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob to a compiled regex.

    Supports `**` (matches any chars including `/`), `*` (any char except
    `/`) and `?` (single char except `/`). Patterns are anchored to the
    repo root, so `backend/**` matches `backend/foo` and
    `backend/foo/bar.py` but not `other/backend/foo`.
    """
    out = ["^"]
    i = 0
    # Handle leading "**/" specially so it matches zero path components too.
    if pattern.startswith("**/"):
        out.append("(?:.*/)?")
        i = 3
    while i < len(pattern):
        c = pattern[i]
        if c == "*" and i + 1 < len(pattern) and pattern[i + 1] == "*":
            out.append(".*")
            i += 2
            if i < len(pattern) and pattern[i] == "/":
                i += 1  # the slash is absorbed by `.*`
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c in r".+(){}[]|^$\\":
            out.append(re.escape(c))
            i += 1
        else:
            out.append(c)
            i += 1
    out.append("$")
    return re.compile("".join(out))


def detect_base_ref(arg: str | None) -> str:
    if arg:
        return arg
    if os.environ.get("GITHUB_BASE_REF"):
        return f"origin/{os.environ['GITHUB_BASE_REF']}"
    if _git_ref_exists("origin/main"):
        return "origin/main"
    if _git_ref_exists("HEAD~1"):
        return "HEAD~1"
    return "HEAD"


def _git_ref_exists(ref: str) -> bool:
    return subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).returncode == 0


def changed_files(base_ref: str) -> list[str]:
    """Return the union of committed changes vs base + currently-staged files."""
    if not _git_ref_exists(base_ref):
        print(
            _color(
                f"check_docs: base ref `{base_ref}` is not reachable.",
                ANSI_RED,
            ),
            file=sys.stderr,
        )
        print(
            "Hint: on GitHub Actions ensure actions/checkout uses `fetch-depth: 0`.",
            file=sys.stderr,
        )
        sys.exit(2)

    def _run(cmd: list[str]) -> list[str]:
        r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            return []
        return [ln for ln in r.stdout.splitlines() if ln.strip()]

    committed = _run([
        "git", "diff", "--name-only", "--diff-filter=ACMR",
        f"{base_ref}...HEAD",
    ])
    staged = _run([
        "git", "diff", "--cached", "--name-only", "--diff-filter=ACMR",
    ])
    seen: set[str] = set()
    ordered: list[str] = []
    for path in committed + staged:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def load_rules() -> list[dict]:
    if not RULES_PATH.exists():
        print(
            _color(f"check_docs: rules file missing at {RULES_PATH}", ANSI_RED),
            file=sys.stderr,
        )
        sys.exit(2)
    data = yaml.safe_load(RULES_PATH.read_text()) or {}
    rules = data.get("rules") or []
    if not isinstance(rules, list):
        print(
            _color("check_docs: rules: must be a list", ANSI_RED),
            file=sys.stderr,
        )
        sys.exit(2)
    return rules


def _match_any(regexes: list[re.Pattern[str]], files: list[str]) -> list[str]:
    return [f for f in files if any(rx.search(f) for rx in regexes)]


def evaluate(rules: list[dict], files: list[str]) -> tuple[list[dict], list[dict]]:
    """Return (violations, passes) where each entry is a dict with rule
    metadata + the files that triggered or satisfied it."""
    violations: list[dict] = []
    passes: list[dict] = []
    for rule in rules:
        name = rule.get("name") or "<unnamed>"
        when = rule.get("when") or []
        require = rule.get("require") or []
        if not when or not require:
            continue
        when_re = [glob_to_regex(p) for p in when]
        require_re = [glob_to_regex(p) for p in require]
        triggers = _match_any(when_re, files)
        if not triggers:
            continue
        missing = []
        for pat, rx in zip(require, require_re):
            if not any(rx.search(f) for f in files):
                missing.append(pat)
        entry = {
            "name": name,
            "description": (rule.get("description") or "").strip(),
            "triggered_by": triggers,
            "require": require,
            "missing": missing,
        }
        (violations if missing else passes).append(entry)
    return violations, passes


def report_text(violations: list[dict], passes: list[dict], files: list[str]) -> None:
    print(_color("check_docs", ANSI_BOLD) + f" — {len(files)} changed file(s) vs base")
    for entry in passes:
        print(_color(f"  ✓ {entry['name']}", ANSI_GREEN))
    if not violations:
        print(_color("All documentation rules satisfied.", ANSI_GREEN))
        return
    print()
    print(_color("=" * 72, ANSI_BOLD))
    print(_color(f"✗ {len(violations)} rule(s) violated", ANSI_RED))
    print(_color("=" * 72, ANSI_BOLD))
    for entry in violations:
        print()
        print(_color(f"  ✗ {entry['name']}", ANSI_RED))
        if entry["description"]:
            print(f"     {_color(entry['description'], ANSI_YELLOW)}")
        print("     Triggered by:")
        for t in entry["triggered_by"]:
            print(f"       - {t}")
        print("     Missing required doc update(s):")
        for m in entry["missing"]:
            print(f"       - {_color(m, ANSI_RED)}")
    print()
    print(_color("Reference:", ANSI_BOLD), "docs/DOC_UPDATE_POLICY.md")


def report_json(violations: list[dict], passes: list[dict], files: list[str]) -> None:
    print(json.dumps(
        {"files": files, "passes": passes, "violations": violations},
        indent=2,
    ))


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    json_output = "--json" in args
    args = [a for a in args if a != "--json"]

    base_ref = detect_base_ref(args[0] if args else None)
    files = changed_files(base_ref)
    rules = load_rules()

    if not files:
        if not json_output:
            print(_color("check_docs: no changes detected.", ANSI_GREEN))
        else:
            report_json([], [], [])
        return 0

    violations, passes = evaluate(rules, files)
    if json_output:
        report_json(violations, passes, files)
    else:
        report_text(violations, passes, files)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
