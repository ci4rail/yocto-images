#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
mkdir -p results
exec .venv/bin/python -m pytest --junitxml=results/junit.xml "$@"
