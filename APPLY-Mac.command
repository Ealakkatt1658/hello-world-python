#!/bin/bash
# Double-click to apply: it asks for the job link, then fills in the application.
cd "$(dirname "$0")" || exit 1
if [ ! -f .venv/bin/activate ]; then
  echo "Run SETUP-Mac.command first."
  read -r -p "Press Enter to close."
  exit 1
fi
source .venv/bin/activate
python -m job_autofill "$@"
read -r -p "Press Enter to close."
