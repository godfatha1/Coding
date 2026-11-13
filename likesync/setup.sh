#!/usr/bin/env bash
# One command from a fresh clone to a scheduled weekly sync:
#
#     ./setup.sh
#
# Installs likesync into ./venv, fetches the browser it drives, then hands over
# to `likesync setup`, which signs you in, checks both libraries are readable
# and installs the weekly job. Safe to run again at any time: finished steps
# are skipped. Extra arguments are passed straight to `likesync setup`, e.g.
#
#     ./setup.sh --spotify-client-id abc123 --weekday sat --at 09:00
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "likesync needs Python 3.11 or newer (found: $("$PYTHON" --version 2>&1 || echo none))." >&2
  echo "Install it from https://www.python.org/downloads/ and run this again." >&2
  exit 1
fi

if [ ! -x venv/bin/python ]; then
  echo "Creating a private Python environment in ./venv ..."
  "$PYTHON" -m venv venv
fi

echo "Installing likesync ..."
venv/bin/python -m pip install --quiet --upgrade pip
# Editable, so a later `git pull` updates the installed tool as well.
venv/bin/python -m pip install --quiet -e '.[web,crypt]'

echo "Fetching the browser likesync drives (skipped if already present) ..."
if ! venv/bin/python -m playwright install chromium >/dev/null 2>&1; then
  # Not fatal: likesync falls back to a Chrome or Chromium already installed.
  echo "  Could not download it; will use a browser already on this machine if there is one."
fi

exec venv/bin/python -m likesync setup "$@"
