#!/usr/bin/env python3
"""check_theme.py — Chiro Software theme-compliance guardrail.

Enforces the "no raw palette / no raw hex in feature code" rule from the
engineering spec at /app/docs/theme/CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md
(see section 15 "Guardrails and Linting").

Scans frontend source for:

  1. Raw hex color literals inside class strings, e.g. ``bg-[#abcdef]``,
     ``text-[#abc]``, ``border-[#112233]``, ``from-[#...]``, etc.
  2. Raw Tailwind palette utility classes outside the approved token layer,
     e.g. ``bg-slate-500``, ``text-blue-600``, ``dark:bg-zinc-900``,
     ``border-stone-100``.
  3. Inline ``style={{ color: "#..." }}`` / ``style="color:#..."`` usages.

Files allow-listed (theme layer / shadcn primitives):

  - ``frontend/src/index.css`` — token definitions.
  - ``frontend/tailwind.config.js`` — token mapping.
  - ``frontend/src/components/ui/**`` — shadcn primitives; these must
    consume semantic tokens too, but minor utility residue left by the
    shadcn CLI is tolerated while Phase 2 migration progresses.

Exit codes:

  - 0: no violations.
  - 1: violations found (listed with file:line).
  - 2: script usage error.

Usage:

  python scripts/check_theme.py                   # scan repo
  python scripts/check_theme.py --paths a.jsx b.jsx   # scan specific files
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "frontend" / "src"

# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

# Tailwind palette families we reject in feature code. These are either
# decorative (purple, fuchsia) or neutrals that should come from semantic
# tokens (slate, gray, stone, zinc).
FORBIDDEN_PALETTES = (
    "slate", "blue", "green", "red", "yellow", "purple", "indigo",
    "gray", "zinc", "pink", "rose", "orange", "amber", "emerald",
    "cyan", "sky", "violet", "fuchsia", "lime", "stone",
)

# Class-modifier prefixes we must also block when combined with a
# forbidden palette: ``bg-``, ``text-``, ``border-``, ``ring-``,
# ``divide-``, ``from-``, ``to-``, ``via-``, plus ``hover:``, ``focus:``,
# ``dark:`` variants.
CLASS_PREFIXES = (
    r"(?:hover:|focus:|focus-visible:|active:|disabled:|dark:|group-hover:|group-focus:|peer-hover:|peer-focus:)?"
    r"(?:bg|text|border|ring|divide|from|to|via|fill|stroke|outline|accent|caret|placeholder|shadow|decoration)-"
)

PALETTE_RE = re.compile(
    rf"\b{CLASS_PREFIXES}(?:{'|'.join(FORBIDDEN_PALETTES)})-(?:\d{{2,3}})(?:\b|/)",
)

# Raw hex in Tailwind arbitrary value syntax, e.g. ``bg-[#abcdef]`` or
# ``text-[#abc]`` / ``border-[#112233]/50``.
HEX_ARBITRARY_RE = re.compile(
    r"(?:bg|text|border|ring|from|to|via|fill|stroke|outline|accent|caret|placeholder|shadow|decoration)-\[#[0-9A-Fa-f]{3,8}\b"
)

# Inline style color declarations.
INLINE_STYLE_HEX_RE = re.compile(
    r"(?:color|background|background-color|border-color|fill|stroke)\s*[:=]\s*[\"']?#[0-9A-Fa-f]{3,8}",
    re.IGNORECASE,
)

# Files / dirs always excluded from the scan (theme layer itself).
EXEMPT_SUFFIXES = (
    "/frontend/src/index.css",
    "/frontend/tailwind.config.js",
)
EXEMPT_DIR_MARKERS = (
    "/frontend/src/components/ui/",
    "/frontend/node_modules/",
    "/frontend/build/",
)

# File extensions we scan.
SCAN_EXTS = {".jsx", ".tsx", ".js", ".ts", ".css"}


def is_exempt(path: str) -> bool:
    p = path.replace(os.sep, "/")
    if any(p.endswith(s) for s in EXEMPT_SUFFIXES):
        return True
    if any(marker in p for marker in EXEMPT_DIR_MARKERS):
        return True
    return False


def iter_source_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in paths:
        if root.is_file():
            files.append(root)
            continue
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                p = Path(dirpath) / name
                if p.suffix in SCAN_EXTS:
                    files.append(p)
    return files


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return [(line_no, category, snippet), ...] violations for a file."""
    if is_exempt(str(path)):
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []

    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines, start=1):
        # Skip comments that merely mention the pattern.
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        if HEX_ARBITRARY_RE.search(line):
            hits.append((i, "raw-hex", line.strip()))
            continue

        if PALETTE_RE.search(line):
            hits.append((i, "raw-palette", line.strip()))
            continue

        if INLINE_STYLE_HEX_RE.search(line):
            hits.append((i, "inline-style-hex", line.strip()))

    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="*",
        help="Specific files to scan (defaults to frontend/src).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only emit the violation count + exit code.",
    )
    args = parser.parse_args()

    roots = [Path(p) for p in (args.paths or [str(SRC)])]
    files = iter_source_files(roots)

    violations: list[tuple[Path, int, str, str]] = []
    for f in files:
        for line_no, cat, snippet in scan_file(f):
            violations.append((f, line_no, cat, snippet))

    if not violations:
        if not args.quiet:
            print(f"check_theme: OK — scanned {len(files)} file(s), "
                  "zero raw-hex / raw-palette violations.")
        return 0

    by_cat: dict[str, int] = {}
    for _, _, cat, _ in violations:
        by_cat[cat] = by_cat.get(cat, 0) + 1

    print(f"check_theme: {len(violations)} violation(s) across {len({v[0] for v in violations})} file(s).",
          file=sys.stderr)
    for cat, n in sorted(by_cat.items()):
        print(f"  - {cat}: {n}", file=sys.stderr)
    print("", file=sys.stderr)

    if args.quiet:
        return 1

    for f, line_no, cat, snippet in violations:
        rel = f.relative_to(REPO) if f.is_absolute() else f
        print(f"{rel}:{line_no}: [{cat}] {snippet}", file=sys.stderr)

    print("", file=sys.stderr)
    print("Fix by consuming semantic tokens: bg-primary, bg-card, "
          "text-muted-foreground, bg-success-soft, border-border, etc.",
          file=sys.stderr)
    print("See /app/docs/theme/CHIRO_THEME_ENGINEERING_IMPLEMENTATION_SPEC.md §8.2",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
