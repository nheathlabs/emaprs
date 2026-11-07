#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Nicholas Heath
# DEV ONLY: run the test suite and code-quality tools; run before committing.
#
#   tests    pytest + coverage (fails below fail_under, or on unclosed
#            resources such as SQLite connections: ResourceWarning)
#   ruff     lint, including complexity (max-complexity)
#   mypy     type check of emaprs/
#   vulture  dead code
#   bandit   security patterns, medium severity and up
#   radon    complexity / maintainability report: never fails
#
# Settings for every tool are in pyproject.toml. Install the tools with
# pip install -e '.[dev]' (tools/install_build_deps.sh does this).
#
# Usage: tools/check.sh              all checks
#        tools/check.sh ruff mypy    only some
#
# Exit status: 0 if every check passed, 1 if any failed, 2 on bad usage.

set -uo pipefail

cd "$(dirname "$0")/.."

BIN=.venv/bin
ALL=(tests ruff mypy vulture bandit radon)

for tool in pytest ruff mypy vulture bandit radon; do
    if [[ ! -x "$BIN/$tool" ]]; then
        echo "check.sh: $BIN/$tool not found; run: .venv/bin/pip install -e '.[dev]'" >&2
        exit 2
    fi
done

selected=("$@")
if [[ ${#selected[@]} -eq 0 ]]; then
    selected=("${ALL[@]}")
fi
for name in "${selected[@]}"; do
    if [[ ! " ${ALL[*]} " == *" $name "* ]]; then
        echo "check.sh: unknown check '$name' (valid: ${ALL[*]})" >&2
        exit 2
    fi
done

if [[ -t 1 ]]; then
    BOLD=$'\e[1m' RED=$'\e[31m' GREEN=$'\e[32m' DIM=$'\e[2m' RESET=$'\e[0m'
else
    BOLD="" RED="" GREEN="" DIM="" RESET=""
fi

declare -A result
failed=0

header() { printf '\n%s==== %s%s\n' "$BOLD" "$1" "$RESET"; }

record() {  # name, exit status
    if [[ $2 -eq 0 ]]; then
        result[$1]="${GREEN}pass${RESET}"
    else
        result[$1]="${RED}FAIL${RESET}"
        failed=1
    fi
}

check_tests() {
    header "tests + coverage (pytest)"
    local out rc
    # -X dev shows ResourceWarnings. An unclosed resource is only reported
    # when garbage-collected, often after its test passed, so pytest itself
    # never fails on it: look for the warning in the output instead.
    out=$("$BIN/python" -X dev -m pytest -q --cov --cov-report=term-missing 2>&1)
    rc=$?
    printf '%s\n' "$out"
    local leaks
    # (Each warning is followed by an "Enable tracemalloc" hint; don't count it.)
    leaks=$(grep "ResourceWarning" <<<"$out" | grep -vc "Enable tracemalloc")
    if [[ $leaks -gt 0 ]]; then
        printf '%s%d ResourceWarning(s): something (a socket, file or database)\n' "$RED" "$leaks"
        printf 'was not closed. Run with -X tracemalloc=20 to see where it was opened.%s\n' "$RESET"
        rc=1
    fi
    record tests $rc
}

check_ruff() {
    header "lint (ruff)"
    # One line per finding; `ruff check FILE` shows the full explanation.
    "$BIN/ruff" check --output-format concise emaprs tests tools
    record ruff $?
}

check_mypy() {
    header "types (mypy)"
    "$BIN/mypy"
    record mypy $?
}

check_vulture() {
    header "dead code (vulture)"
    "$BIN/vulture" && echo "no dead code found"
    record vulture $?
}

check_bandit() {
    header "security (bandit, medium severity and up)"
    "$BIN/bandit" -q -c pyproject.toml -r emaprs --severity-level medium \
        && echo "no issues found"
    record bandit $?
}

check_radon() {
    header "complexity (radon; report only, never fails)"
    echo "Functions graded C or worse (A-B simple, C moderate, D+ complex):"
    "$BIN/radon" cc -s -nc emaprs
    "$BIN/radon" cc -s -a emaprs | tail -1
    echo
    echo "Files below maintainability grade A:"
    local mi
    mi=$("$BIN/radon" mi -s -nb emaprs)
    printf '%s\n' "${mi:-  none}"
    result[radon]="${DIM}report${RESET}"
}

for name in "${selected[@]}"; do
    "check_$name"
done

header "summary"
for name in "${selected[@]}"; do
    printf '  %-8s %s\n' "$name" "${result[$name]}"
done
if [[ $failed -ne 0 ]]; then
    printf '\n%sSome checks failed.%s\n' "$RED" "$RESET"
else
    printf '\n%sAll checks passed.%s\n' "$GREEN" "$RESET"
fi
exit $failed
