#!/usr/bin/env bash
# Run the Multisport Helper in this terminal (the installed service does this for you at boot).
#   ./run.sh              port 8081
#   ./run.sh --port 8091
cd "$(dirname "${BASH_SOURCE[0]}")"
exec .venv/bin/python -m multisport "$@"
