#!/bin/sh
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
#   shell    shell scripts are POSIX sh: #!/bin/sh, shellcheck --shell=sh,
#            and dash -n (skipped if dash isn't installed)
#   radon    complexity / maintainability report: never fails
#
# Settings for the Python tools are in pyproject.toml; shellcheck's are
# its command line and the disable comments in each script. Install the
# tools with pip install -e '.[dev]' (tools/install_build_deps.sh does this).
#
# Usage: tools/check.sh              all checks
#        tools/check.sh ruff mypy    only some
#
# Exit status: 0 if every check passed, 1 if any failed, 2 on bad usage.

# The check_* functions are only called by name, as "check_$name".
# shellcheck disable=SC2329

set -u

cd "$(dirname "$0")/.." || exit 2

BIN=.venv/bin
ALL="tests ruff mypy vulture bandit shell radon"

for tool in pytest ruff mypy vulture bandit shellcheck radon; do
    if [ ! -x "$BIN/$tool" ]; then
        echo "check.sh: $BIN/$tool not found; run: .venv/bin/pip install -e '.[dev]'" >&2
        exit 2
    fi
done

if [ $# -eq 0 ]; then
    # shellcheck disable=SC2086  # split ALL into one argument per check
    set -- $ALL
fi
for name in "$@"; do
    case " $ALL " in
        *" $name "*) ;;
        *)
            echo "check.sh: unknown check '$name' (valid: $ALL)" >&2
            exit 2
            ;;
    esac
done

if [ -t 1 ]; then
    ESC=$(printf '\033')
    BOLD="${ESC}[1m" RED="${ESC}[31m" GREEN="${ESC}[32m" DIM="${ESC}[2m" RESET="${ESC}[0m"
else
    BOLD="" RED="" GREEN="" DIM="" RESET=""
fi

summary=""  # one "  name  result" line per check run, for the summary
failed=0

header() { printf '\n%s==== %s%s\n' "$BOLD" "$1" "$RESET"; }

add_summary() {  # name, result text
    summary="$summary$(printf '  %-8s %s' "$1" "$2")
"
}

record() {  # name, exit status
    if [ "$2" -eq 0 ]; then
        add_summary "$1" "${GREEN}pass${RESET}"
    else
        add_summary "$1" "${RED}FAIL${RESET}"
        failed=1
    fi
}

check_tests() {
    header "tests + coverage (pytest)"
    # -X dev shows ResourceWarnings. An unclosed resource is only reported
    # when garbage-collected, often after its test passed, so pytest itself
    # never fails on it: look for the warning in the output instead.
    tests_out=$("$BIN/python" -X dev -m pytest -q --cov --cov-report=term-missing 2>&1)
    tests_rc=$?
    printf '%s\n' "$tests_out"
    # (Each warning is followed by an "Enable tracemalloc" hint; don't count it.)
    leaks=$(printf '%s\n' "$tests_out" | grep "ResourceWarning" | grep -vc "Enable tracemalloc")
    if [ "$leaks" -gt 0 ]; then
        printf '%s%d ResourceWarning(s): something (a socket, file or database)\n' "$RED" "$leaks"
        printf 'was not closed. Run with -X tracemalloc=20 to see where it was opened.%s\n' "$RESET"
        tests_rc=1
    fi
    record tests "$tests_rc"
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

check_shell() {
    header "shell scripts (POSIX sh)"
    shell_rc=0
    # Every tracked *.sh file. File names have no spaces, so a plain list will do.
    scripts=$(git ls-files '*.sh')
    for f in $scripts; do
        if [ "$(head -n 1 "$f")" != "#!/bin/sh" ]; then
            echo "$f: first line must be #!/bin/sh"
            shell_rc=1
        fi
    done
    # --shell=sh checks against POSIX sh whatever the shebang says.
    # shellcheck disable=SC2086  # one argument per script
    "$BIN/shellcheck" --shell=sh $scripts || shell_rc=1
    # dash is a strict POSIX shell; -n parses without running anything.
    if command -v dash >/dev/null 2>&1; then
        for f in $scripts; do
            dash -n "$f" || shell_rc=1
        done
    else
        echo "dash not found: skipped the dash -n syntax check (CI runs it)"
    fi
    [ "$shell_rc" -eq 0 ] && echo "no issues found"
    record shell "$shell_rc"
}

check_radon() {
    header "complexity (radon; report only, never fails)"
    echo "Functions graded C or worse (A-B simple, C moderate, D+ complex):"
    "$BIN/radon" cc -s -nc emaprs
    "$BIN/radon" cc -s -a emaprs | tail -1
    echo
    echo "Files below maintainability grade A:"
    mi=$("$BIN/radon" mi -s -nb emaprs)
    printf '%s\n' "${mi:-  none}"
    add_summary radon "${DIM}report${RESET}"
}

for name in "$@"; do
    "check_$name"
done

header "summary"
printf '%s' "$summary"
if [ $failed -ne 0 ]; then
    printf '\n%sSome checks failed.%s\n' "$RED" "$RESET"
else
    printf '\n%sAll checks passed.%s\n' "$GREEN" "$RESET"
fi
exit $failed
