#!/usr/bin/env bash
set -e
cd -- "$(dirname -- "$0")"

if [[ ! -t 0 || ! -t 1 ]]; then
    printf '%s\n' 'Bookeeper: run this script in an interactive terminal.' >&2
    exit 1
fi

if [[ -x .venv/Scripts/python.exe ]]; then
    python=.venv/Scripts/python.exe
    if command -v winpty >/dev/null; then
        exec winpty "$python" -m flask --app bookeeper create-first-user
    fi
elif [[ -x .venv/bin/python ]]; then
    python=.venv/bin/python
else
    printf '%s\n' 'Bookeeper: create and install the .venv first (see README.md).' >&2
    exit 1
fi

exec "$python" -m flask --app bookeeper create-first-user
