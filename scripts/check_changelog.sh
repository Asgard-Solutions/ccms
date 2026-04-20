#!/usr/bin/env bash
#
# check_changelog.sh — fail when backend/ or frontend/ code changed without
# a matching CHANGELOG.md update.
#
# Usage:
#     scripts/check_changelog.sh <base_ref>
#
# If <base_ref> is omitted the script auto-detects:
#   1. $GITHUB_BASE_REF (set by GitHub Actions on pull_request events)
#   2. origin/main
#   3. HEAD~1
#
# Exit codes:
#   0 — OK (either no code changes or CHANGELOG updated).
#   1 — violation: code changed but CHANGELOG.md not touched.
#   2 — usage / environment error.
#
set -euo pipefail

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
red()  { printf '\033[31m%s\033[0m\n' "$*"; }
green(){ printf '\033[32m%s\033[0m\n' "$*"; }

detect_base_ref() {
  if [[ -n "${1:-}" ]]; then echo "$1"; return; fi
  if [[ -n "${GITHUB_BASE_REF:-}" ]]; then echo "origin/${GITHUB_BASE_REF}"; return; fi
  if git rev-parse --verify --quiet origin/main >/dev/null; then echo "origin/main"; return; fi
  if git rev-parse --verify --quiet HEAD~1 >/dev/null; then echo "HEAD~1"; return; fi
  echo "HEAD"
}

BASE_REF="$(detect_base_ref "${1:-}")"

if ! git rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
  red  "check_changelog: unable to resolve base ref '${BASE_REF}'."
  echo "Hint: for pre-commit runs, stage your changes and re-run;"
  echo "      for CI runs, ensure actions/checkout uses fetch-depth: 0."
  exit 2
fi

# Files changed compared to the base. --name-only + --diff-filter=ACMR
# ignores pure deletions.
CHANGED=$(git diff --name-only --diff-filter=ACMR "${BASE_REF}"...HEAD 2>/dev/null || true)

# Include the staging area when running as a pre-commit hook.
STAGED=$(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null || true)
ALL_FILES=$(printf '%s\n%s\n' "$CHANGED" "$STAGED" | sort -u | sed '/^$/d')

if [[ -z "$ALL_FILES" ]]; then
  green "check_changelog: no changes detected."
  exit 0
fi

CODE_CHANGES=$(echo "$ALL_FILES" | grep -E '^(backend|frontend)/' || true)
if [[ -z "$CODE_CHANGES" ]]; then
  green "check_changelog: no backend/ or frontend/ changes — CHANGELOG update not required."
  exit 0
fi

CHANGELOG_TOUCHED=$(echo "$ALL_FILES" | grep -E '^CHANGELOG\.md$' || true)
if [[ -n "$CHANGELOG_TOUCHED" ]]; then
  green "check_changelog: CHANGELOG.md updated alongside code changes ✓"
  exit 0
fi

bold "========================================================================"
red  "✗ check_changelog: backend/ or frontend/ changes require a CHANGELOG entry."
bold "========================================================================"
echo "Changed code files detected:"
echo "$CODE_CHANGES" | sed 's/^/    /'
echo
echo "Append an entry to the [Unreleased] section of /app/CHANGELOG.md"
echo "(see docs/DOC_UPDATE_POLICY.md for guidance), then re-run:"
echo
echo "    scripts/check_changelog.sh ${BASE_REF}"
echo
exit 1
