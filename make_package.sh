#!/bin/bash
# Builds offer_reloader.zip to send to the user: only the files she needs, no tokens/venv/sandbox stuff.
cd "$(dirname "$0")" || exit 1
rm -f offer_reloader.zip
zip -q offer_reloader.zip main.py auth.py creds.py requirements.txt run.command run.bat README.md
echo "Built offer_reloader.zip:"; unzip -l offer_reloader.zip
