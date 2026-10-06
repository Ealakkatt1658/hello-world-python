#!/bin/bash
# Double-click once: installs everything and asks the questions for your profile.
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "Python 3.10 or newer isn't installed. Opening the download page..."
  echo "Install it, then double-click this file again."
  open https://www.python.org/downloads/
  read -r -p "Press Enter to close."
  exit 1
fi
fail() { echo; echo "Something went wrong above. Take a screenshot of this window and send it to Claude."; read -r -p "Press Enter to close."; exit 1; }
echo "Installing (this takes a few minutes the first time)..."
[ -d .venv ] || python3 -m venv .venv || fail
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt || fail
python -m playwright install chromium || fail
python -m job_autofill --setup
echo
echo "Done. To apply to a job, double-click APPLY-Mac.command"
read -r -p "Press Enter to close."
