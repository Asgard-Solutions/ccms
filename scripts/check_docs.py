#!/usr/bin/env python3
"""
check_docs.py — matrix-aware documentation update guard.

Reads `/app/docs/doc_rules.yml` and fails whenever a PR (or local commit)
touches files matched by a rule's `when` globs without also updating every
doc listed under that rule's `require`.

Usage:
    scripts/check_docs.py [base_ref] [--json]
        Run the guard. Exit 0 if all rules pass, 1 if any rule is violated.

    scripts/check_docs.py --emit-changelog-stub [base_ref] \\
        [--title "Short summary"] [--category Added|Changed|Fixed|Security|Dependencies] \\
        [--write] [--json]
        Suggest (or write) a CHANGELOG.md bullet for the current diff.
        Without --write the stub is printed to stdout. With --write the
        bullet is inserted under `## [Unreleased]` → `### <category>`,
        creating the scaffolding if missing. Idempotent.

    base_ref auto-detection (when omitted):
        1. $GITHUB_BASE_REF (set by GitHub Actions on pull_request)
        2. origin/main
        3. HEAD~1

Exit codes:
    0  all rules satisfied (or stub generated)
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


# ---------------------------------------------------------------------------
# --emit-changelog-stub
# ---------------------------------------------------------------------------

CATEGORY_SECURITY_PATHS = (
    "backend/core/audit.py",
    "backend/core/crypto.py",
    "backend/core/masking.py",
    "backend/core/reauth.py",
    "backend/core/security.py",
    "backend/core/password_policy.py",
    "backend/core/mfa.py",
    "backend/services/authz/",
)
CATEGORY_DEPS_PATHS = (
    "backend/requirements.txt",
    "frontend/package.json",
)
FIX_SUBJECT_RE = re.compile(r"^(fix|fixes|fixed|bug|patch|hotfix|revert)[:(\s]", re.IGNORECASE)


def _guess_category(files: list[str], subjects: list[str]) -> str:
    """Pick a CHANGELOG section for the stub using simple heuristics."""
    if any(f == p or f.startswith(p) for f in files for p in CATEGORY_SECURITY_PATHS):
        return "Security"
    if any(f in CATEGORY_DEPS_PATHS for f in files):
        return "Dependencies"
    if any(FIX_SUBJECT_RE.match(s or "") for s in subjects):
        return "Fixed"
    return "Added"


def _git_subjects(base_ref: str) -> list[str]:
    if not _git_ref_exists(base_ref):
        return []
    r = subprocess.run(
        ["git", "log", "--format=%s", f"{base_ref}..HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _derive_title(subjects: list[str], fallback_files: list[str]) -> str:
    for s in subjects:
        if s and not s.lower().startswith(("merge ", "wip", "fixup!")):
            return s[:120]
    if fallback_files:
        first = fallback_files[0]
        if len(fallback_files) == 1:
            return f"Update `{first}`"
        return f"Update `{first}` and {len(fallback_files) - 1} other file(s)"
    return "Describe this change"


def build_changelog_stub(
    *,
    files: list[str],
    subjects: list[str],
    title: str | None,
    category: str | None,
) -> tuple[str, str]:
    """Return (category, bullet_line) for the suggested CHANGELOG entry."""
    cat = category or _guess_category(files, subjects)
    head = (title or _derive_title(subjects, files)).strip().rstrip(".")
    # Cap file list to keep the bullet readable.
    code_files = [f for f in files if f.startswith(("backend/", "frontend/"))]
    scope = ""
    if code_files:
        shown = code_files[:3]
        tail = f" (+{len(code_files) - 3} more)" if len(code_files) > 3 else ""
        scope = " — affects " + ", ".join(f"`{f}`" for f in shown) + tail
    bullet = f"- {head}{scope}."
    return cat, bullet


def write_stub_to_changelog(category: str, bullet: str) -> str:
    """Insert `bullet` under `### <category>` inside the `## [Unreleased]`
    section of CHANGELOG.md. Create any missing scaffolding. Returns the
    action taken as a human-readable string."""
    path = REPO_ROOT / "CHANGELOG.md"
    if not path.exists():
        raise FileNotFoundError("CHANGELOG.md not found at repo root")
    original = path.read_text()
    lines = original.splitlines()

    # Idempotency — don't duplicate a bullet that already exists.
    for ln in lines:
        if ln.strip() == bullet.strip():
            return "no-op (bullet already present)"

    # Locate `## [Unreleased]`.
    un_idx = next(
        (i for i, ln in enumerate(lines) if ln.strip().lower().startswith("## [unreleased]")),
        None,
    )
    if un_idx is None:
        # Insert an [Unreleased] block at the top of the first "## " section.
        insert_at = next(
            (i for i, ln in enumerate(lines) if ln.startswith("## ")),
            len(lines),
        )
        block = ["## [Unreleased]", "", f"### {category}", bullet, ""]
        lines[insert_at:insert_at] = block
        path.write_text("\n".join(lines) + "\n")
        return "created [Unreleased] block"

    # Find next `## ` to bound the [Unreleased] section.
    next_release_idx = next(
        (i for i in range(un_idx + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )

    # Look for an existing `### <category>` header inside the block.
    cat_idx = next(
        (
            i
            for i in range(un_idx + 1, next_release_idx)
            if lines[i].strip().lower() == f"### {category}".lower()
        ),
        None,
    )
    if cat_idx is not None:
        # Append the bullet at the end of this subsection (before the next `###` or blank).
        end_idx = next(
            (
                i
                for i in range(cat_idx + 1, next_release_idx)
                if lines[i].startswith("### ")
            ),
            next_release_idx,
        )
        # Walk back past trailing blank lines.
        insert_idx = end_idx
        while insert_idx - 1 > cat_idx and not lines[insert_idx - 1].strip():
            insert_idx -= 1
        lines.insert(insert_idx, bullet)
        path.write_text("\n".join(lines) + "\n")
        return f"appended under existing ### {category}"

    # No subsection yet — insert a new `### <category>` right after the header.
    insert_idx = un_idx + 1
    # Preserve a blank line after the heading if present.
    if insert_idx < len(lines) and lines[insert_idx].strip() == "":
        insert_idx += 1
    new_block = [f"### {category}", bullet, ""]
    lines[insert_idx:insert_idx] = new_block
    path.write_text("\n".join(lines) + "\n")
    return f"added new ### {category} section"


def emit_changelog_stub(
    base_ref: str,
    *,
    title: str | None,
    category: str | None,
    write: bool,
    json_output: bool,
) -> int:
    files = changed_files(base_ref)
    if not files:
        msg = "emit_changelog_stub: no changes vs base — nothing to suggest."
        if json_output:
            print(json.dumps({"status": "noop", "reason": msg}))
        else:
            print(_color(msg, ANSI_YELLOW))
        return 0

    subjects = _git_subjects(base_ref)
    cat, bullet = build_changelog_stub(
        files=files, subjects=subjects, title=title, category=category,
    )
    payload = {
        "category": cat,
        "bullet": bullet,
        "suggested_block": f"## [Unreleased]\n\n### {cat}\n{bullet}\n",
    }
    if write:
        try:
            action = write_stub_to_changelog(cat, bullet)
        except FileNotFoundError as exc:
            print(_color(f"emit_changelog_stub: {exc}", ANSI_RED), file=sys.stderr)
            return 2
        payload["status"] = "written"
        payload["action"] = action
        if json_output:
            print(json.dumps(payload, indent=2))
        else:
            print(_color(f"CHANGELOG.md updated — {action}:", ANSI_GREEN))
            print(f"  ### {cat}")
            print(f"  {bullet}")
    else:
        payload["status"] = "preview"
        if json_output:
            print(json.dumps(payload, indent=2))
        else:
            print(_color("Suggested CHANGELOG stub (preview, not written):", ANSI_BOLD))
            print()
            print(payload["suggested_block"])
            print(_color("To apply, re-run with --write.", ANSI_YELLOW))
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    json_output = "--json" in args
    emit_stub = "--emit-changelog-stub" in args
    write = "--write" in args

    title: str | None = None
    category: str | None = None
    cleaned: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--json", "--emit-changelog-stub", "--write"):
            i += 1
            continue
        if a == "--title" and i + 1 < len(args):
            title = args[i + 1]
            i += 2
            continue
        if a.startswith("--title="):
            title = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--category" and i + 1 < len(args):
            category = args[i + 1]
            i += 2
            continue
        if a.startswith("--category="):
            category = a.split("=", 1)[1]
            i += 1
            continue
        if a in ("-h", "--help"):
            print(__doc__)
            return 0
        cleaned.append(a)
        i += 1

    base_ref = detect_base_ref(cleaned[0] if cleaned else None)

    if emit_stub:
        return emit_changelog_stub(
            base_ref, title=title, category=category,
            write=write, json_output=json_output,
        )

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
