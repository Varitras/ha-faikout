#!/bin/sh
# Definition of done, as one command.
#
#   PYTHON=~/ha-test/venv/bin/python ./check.sh
#
# The interpreter arrives by environment variable so no machine-local path
# lives in here. It needs ruff, mypy and the Home Assistant test stack, which
# on Windows means WSL: Home Assistant's test machinery imports fcntl.
# Stops at the first failure; a gate that cannot run says so instead of
# waving the run through.
set -eu
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
PACKAGE="custom_components/faikout"

step() { printf '\n== %s ==\n' "$1"; }

# No `ruff format --check`: the code is written to 100 columns and adopting the
# formatter would reflow every file without making anything clearer. The linter
# alone is the deliberate choice here, and the CI lint job matches it.
step "ruff check"
"$PYTHON" -m ruff check .

step "mypy"
"$PYTHON" -m mypy "$PACKAGE"

step "pytest"
"$PYTHON" -m pytest tests/ --cov="$PACKAGE" --cov-fail-under=90

step "dead code (vulture)"
if "$PYTHON" -c "import vulture" 2>/dev/null; then
    # The ignored names are parameters fixed by someone else's contract:
    # entry_data is how Home Assistant calls async_step_reauth, the rest is
    # the positional signature paho hands its thread callbacks.
    "$PYTHON" -m vulture "$PACKAGE" --min-confidence 90 --ignore-names "entry_data,userdata,flags,properties,args"
else
    echo "SKIPPED: vulture not installed - dead parallel stacks stay invisible"
fi

printf '\nAll gates green.\n'
