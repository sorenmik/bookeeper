#!/usr/bin/env bash
set -e
cd -- "$(dirname -- "$0")"

if [[ -x .venv/Scripts/python.exe ]]; then
    python=.venv/Scripts/python.exe
elif [[ -x .venv/bin/python ]]; then
    python=.venv/bin/python
else
    printf '%s\n' 'Bookeeper: create and install the .venv first (see README.md).' >&2
    exit 1
fi

exec "$python" -m bookeeper.service "$@"
