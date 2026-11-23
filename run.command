#!/bin/bash
# macOS launcher: double-click to run against the real Allegro account.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "First run: setting up (takes a minute)..."
    python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || { read -rp "Setup failed. Press Enter to close..."; exit 1; }
fi
.venv/bin/python main.py --env prod --ask
echo
read -rp "Press Enter to close..."
